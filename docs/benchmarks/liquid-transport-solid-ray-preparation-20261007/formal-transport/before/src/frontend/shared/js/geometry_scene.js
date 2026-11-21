/**
 * geometry_scene.js — the half of the geometry builder that touches a scene.
 *
 * 51 functions and 8 private caches/constants lifted verbatim out of
 * STL_generate.html: the model renderers (rack, custom, partition wall, box,
 * planar), mesh spawning and merging, material and texture caching, and the STL
 * grouping entry point buildGroupedStlMap itself.
 *
 * These could not go into js/geometry_pure.js because they read live scene
 * state. Rather than reimplement them per app, the state is INJECTED: a caller
 * passes a context and gets back the API bound to it, so the legacy editor and
 * the React editor run the same code over their own scenes.
 *
 * The context (11 entries):
 *   state, uiState             — the project and the UI state; both are
 *                                REASSIGNED wholesale on project load, so pass
 *                                them as accessors, not as snapshots
 *   renderMeshes, renderRoots,
 *   renderHelpers              — also reassigned (`renderMeshes = []` on clear)
 *   webglAvailable             — reassigned once at startup
 *   globalGroup, wrapperGroup  — stable THREE.Group references
 *   geometryCache, _renderRootMap, _modelMap  — stable Maps
 *
 * Six of the eleven are reassigned, which is why the context must expose them
 * through getters. Handing over the current array would leave this module
 * writing into an array the page has already thrown away — the meshes would
 * build, and nothing would ever appear.
 *
 * Two shadowing cases are preserved rather than rewritten, and they are the
 * reason the rewrite is name-aware instead of textual:
 *   - `_buildServerFaceWithPatches(..., renderMeshes, ...)` takes renderMeshes
 *     as a PARAMETER. Inside it the name means the argument, not the context.
 *   - `exportMeshByRegions(..., targetParent = globalGroup)` references the
 *     context from a DEFAULT VALUE, which does get rewritten.
 *
 * Verified byte for byte through parity/stl_oracle.py, and visually — moving
 * renderers can leave the export correct while the viewport is blank.
 *
 * `THREE`, `window.geometryPure`, `window.stlNaming` and `window.stlWriter`
 * must be available before this is used.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.geometryScene = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    // Per-export evidence, keyed by the exact returned STL map. Region identity
    // comes from the OO model invocation that emitted its original triangles;
    // it does not assert an OO physical face or an editable triangle identity.
    const groupedSourceCatalogues = new WeakMap();
    function getGroupedStlSourceCatalogue(files) {
        return groupedSourceCatalogues.get(files) || null;
    }


    // Sibling modules, bound explicitly.
    //
    // These used to resolve as bare globals. In the legacy page that worked by
    // accident: a top-level `function` in a classic <script> becomes a window
    // property, so a call here found the page's forwarding alias. It is not a
    // dependency this file declares, and it makes the module unusable anywhere
    // else — React imports it and there are no such globals.
    //
    // Lazily resolved, because script order is not guaranteed across apps.
    function _pure() {
        var m = (typeof window !== 'undefined') ? window.geometryPure : null;
        if (!m) throw new Error('geometry_scene.js needs js/geometry_pure.js loaded first');
        return m;
    }
    function _naming() {
        var m = (typeof window !== 'undefined') ? window.stlNaming : null;
        if (!m) throw new Error('geometry_scene.js needs js/stl_naming.js loaded first');
        return m;
    }
    function _writer() {
        var m = (typeof window !== 'undefined') ? window.stlWriter : null;
        if (!m) throw new Error('geometry_scene.js needs js/stl_writer.js loaded first');
        return m;
    }

    function _flipGeometryWinding() { return _pure()._flipGeometryWinding.apply(null, arguments); }
    function _floorBodyStyle() { return _pure()._floorBodyStyle.apply(null, arguments); }
    function _generateProceduralTexture() { return _pure()._generateProceduralTexture.apply(null, arguments); }
    function _rackDoorShaderMaterial() { return _pure()._rackDoorShaderMaterial.apply(null, arguments); }
    function _renderLiquidManifold() { return _pure()._renderLiquidManifold.apply(null, arguments); }
    function applyModelTransformToObject() { return _pure().applyModelTransformToObject.apply(null, arguments); }
    function buildFaceIslands() { return _pure().buildFaceIslands.apply(null, arguments); }
    function buildPartitionWallHoleCutterGeometry() { return _pure().buildPartitionWallHoleCutterGeometry.apply(null, arguments); }
    function buildPartitionWallHoleProxyGeometry() { return _pure().buildPartitionWallHoleProxyGeometry.apply(null, arguments); }
    function canUseSurfaceProjectionFallback() { return _pure().canUseSurfaceProjectionFallback.apply(null, arguments); }
    function circleToPoly() { return _pure().circleToPoly.apply(null, arguments); }
    function classifyTriangleRegion() { return _pure().classifyTriangleRegion.apply(null, arguments); }
    function computeConvexHull2D() { return _pure().computeConvexHull2D.apply(null, arguments); }
    function disposeRenderObject() { return _pure().disposeRenderObject.apply(null, arguments); }
    function ensurePatchProjectionDefaults() { return _pure().ensurePatchProjectionDefaults.apply(null, arguments); }
    function ensureWallOpacity() { return _pure().ensureWallOpacity.apply(null, arguments); }
    function exportPlanarPolygons() { return _pure().exportPlanarPolygons.apply(null, arguments); }
    function getAllModelPatches() { return _pure().getAllModelPatches.apply(null, arguments); }
    function getCustomPatchBasis() { return _pure().getCustomPatchBasis.apply(null, arguments); }
    function getCustomPatchPosition() { return _pure().getCustomPatchPosition.apply(null, arguments); }
    function getDefaultPatchEuler() { return _pure().getDefaultPatchEuler.apply(null, arguments); }
    function getEquipmentFaceColor() { return _pure().getEquipmentFaceColor.apply(null, arguments); }
    function getFaceLayouts() { return _pure().getFaceLayouts.apply(null, arguments); }
    function getPartitionDepthRange() { return _pure().getPartitionDepthRange.apply(null, arguments); }
    function getPartitionWallGeometrySignature() { return _pure().getPartitionWallGeometrySignature.apply(null, arguments); }
    function getPartitionWallPatchStore() { return _pure().getPartitionWallPatchStore.apply(null, arguments); }
    function getPatchVector() { return _pure().getPatchVector.apply(null, arguments); }
    function getPlanarRegionFrame() { return _pure().getPlanarRegionFrame.apply(null, arguments); }
    function getRackMergedEmptySlotRanges() { return _pure().getRackMergedEmptySlotRanges.apply(null, arguments); }
    function getRackMergedSlotExportName() { return _pure().getRackMergedSlotExportName.apply(null, arguments); }
    function getRackPartitionExportName() { return _pure().getRackPartitionExportName.apply(null, arguments); }
    function getRackSlotBounds() { return _pure().getRackSlotBounds.apply(null, arguments); }
    function getRegionReferenceInfos() { return _pure().getRegionReferenceInfos.apply(null, arguments); }
    // A server's ABSOLUTE depth range: z0 = server.inletOffset, z1 = D -
    // server.outletOffset, from the shared helper in geometry_pure.js. The
    // rack-level row offsets (model.inletOffset / model.outletOffset) take no
    // part in it — adding them back here is what used to make a U-slot row
    // slider drag reposition every server in the rack.
    function getServerDepthRange(model, equipment) {
        var pure = _pure();
        if (typeof pure.getServerDepthRange === 'function') return pure.getServerDepthRange(model, equipment);
        // The same arithmetic, for a geometry_pure.js that predates the helper —
        // minus its clamps and its backfill of the retired rack-relative extras.
        var z0 = Number(equipment && equipment.inletOffset) || 0;
        var z1 = (Number(model && model.geometry && model.geometry.size.z) || 0)
            - (Number(equipment && equipment.outletOffset) || 0);
        return { z0: z0, z1: Math.max(z0, z1) };
    }
    function getServerFaceExportName() { return _pure().getServerFaceExportName.apply(null, arguments); }
    function getServerFaceRect() { return _pure().getServerFaceRect.apply(null, arguments); }
    function getSlotDepthRange() { return _pure().getSlotDepthRange.apply(null, arguments); }
    function isPrimaryFaceSolidNormalReversed() { return _pure().isPrimaryFaceSolidNormalReversed.apply(null, arguments); }
    function isRackSlotDeleted() { return _pure().isRackSlotDeleted.apply(null, arguments); }
    function isRackSlotSelectable() { return _pure().isRackSlotSelectable.apply(null, arguments); }
    function isWallFloorOrientationEligible() { return _pure().isWallFloorOrientationEligible.apply(null, arguments); }
    function rectToPoly() { return _pure().rectToPoly.apply(null, arguments); }
    function regionGeometryToPolygons() { return _pure().regionGeometryToPolygons.apply(null, arguments); }
    function syncPartitionWallModelState() { return _pure().syncPartitionWallModelState.apply(null, arguments); }
    function toFramePoint() { return _pure().toFramePoint.apply(null, arguments); }
    function composeModelScopedExportName() { return _naming().composeModelScopedExportName.apply(null, arguments); }
    function getPatchFinalExportName() { return _naming().getPatchFinalExportName.apply(null, arguments); }
    function getRackServerViews() { return _naming().getRackServerViews.apply(null, arguments); }
    function getRackThroughZoneGroups() { return _naming().getRackThroughZoneGroups.apply(null, arguments); }
    function sanitizeName() { return _naming().sanitizeName.apply(null, arguments); }
    function classifyRackSolidToBucketFile() { return _naming().classifyRackSolidToBucketFile.apply(null, arguments); }
    function _classifyRackSolidToBucketFile() { return _naming().classifyRackSolidToBucketFile.apply(null, arguments); }
    function exportToStlString() { return _writer().exportToStlString.apply(null, arguments); }
    function appendMeshFacets() { return _writer().appendMeshFacets.apply(null, arguments); }
    function _getTriangleFacetData() { return _writer().getTriangleFacetData.apply(null, arguments); }

    function createGeometryScene(ctx) {
        // `onTextureLoaded` is deliberately NOT required — see its call site.
    // `allModels` — the flat construction list, from the context.
    //
    // Was `_allModels()`, another page global that resolved only inside
    // the legacy editor. Order matters: the .liquidrun and .cfdrun consumers
    // index into this list, so it must be the SHARED module's ordering
    // (cfdprojStateV4.allModels), not a re-derived one.
    function _allModels() {
        if (typeof ctx.allModels === 'function') return ctx.allModels();
        var m = (typeof window !== 'undefined') ? window.cfdprojStateV4 : null;
        if (!m || !ctx.state) return [];
        return m.allModels(ctx.state);
    }

        var required = ['state', 'uiState', 'renderMeshes', 'renderRoots', 'renderHelpers',
                        'globalGroup', 'wrapperGroup', 'geometryCache', '_renderRootMap', '_modelMap'];
        for (var i = 0; i < required.length; i++) {
            if (!(required[i] in ctx)) {
                throw new Error('geometry_scene: context is missing ' + required[i]);
            }
        }

            const _materialCache = {};

            const _textureCache = {};

            // Physical size (m) of one texture tile per objectKind. Planar
            // geometry (ShapeGeometry / spawnPlanarRect) carries its UVs in
            // METRES, so the repeat that shows one tile per `tileSize` metres
            // is 1 / tileSize — the same for every mesh of the kind.
            const TEXTURE_TILE_SIZES = { 'rack-side': 0.3, 'rack-front': 0.3, 'rack-top': 0.3, 'rack-bottom': 0.3,
                'server-front': 0.15, 'blank-panel': 0.3, 'floor': 0.6, 'floor-vent': 0.3, 'wall': 0.5, 'baffle': _baffleMaterials().preset('baffle').tileM, 'box': _boxMaterialRecipes().boxPreset().tileM, 'acu-supply': 0.25, 'acu-return': 0.3,
                'server-outlet-mesh': 0.1, 'baffle-grille': _baffleMaterials().preset('baffle-grille').tileM };

            // Textures at a given repeat, keyed by (base texture, repeat).
            //
            // The kind's textures used to be SHARED by every mesh of the kind
            // while spawnRenderable wrote a per-mesh repeat onto them, so the
            // last mesh built set the tiling of all of them. A material now
            // gets its own clone per distinct repeat; a clone shares the
            // image (`Texture.source`), so the GPU still holds one copy.
            const _repeatTextureCache = new Map();

            const _materialPresets = {
            'rack-side':   { metalness: 0.3,  roughness: 0.65, normalScale: 0.4, envMapIntensity: 0.5,  tintColor: 0x3a3d42 },  // dark gunmetal
            'rack-front':  { metalness: 0.2,  roughness: 0.7,  normalScale: 0.5, envMapIntensity: 0.3,  tintColor: 0x2e3138 },  // dark steel
            'rack-top':    { metalness: 0.25, roughness: 0.7,  normalScale: 0.3, envMapIntensity: 0.4,  tintColor: 0x3a3d42 },  // match sides
            'rack-bottom': { metalness: 0.25, roughness: 0.75, normalScale: 0.3, envMapIntensity: 0.3,  tintColor: 0x3a3d42 },
            'server-front':{ metalness: 0.35, roughness: 0.55, normalScale: 0.6, envMapIntensity: 0.4,  tintColor: 0x1a1e24 },  // near-black server faces
            'blank-panel': { metalness: 0.02, roughness: 0.92, normalScale: 0.15, envMapIntensity: 0.08, tintColor: 0xd0ddd0 },  // light gray-green plastic (like real APC blanking panels)
            'floor':       { metalness: 0.05, roughness: 0.85, normalScale: 0.3, envMapIntensity: 0.15, tintColor: 0xb8bcc2 },  // light concrete gray
            'wall':        { metalness: 0.0,  roughness: 0.9,  normalScale: 0.35, envMapIntensity: 0.1, tintColor: 0xd0d2d5 },  // pale concrete
            'baffle':      _baffleMaterials().preset('baffle'),  // galvanized silver
            'box':         _boxMaterialRecipes().boxPreset(),       // use model color
            'floor-vent':  { metalness: 0.4,  roughness: 0.5,  normalScale: 0.7, envMapIntensity: 0.4,  tintColor: 0x607868 },  // greenish aluminum (anti-static)
            'acu-supply':  { metalness: 0.35, roughness: 0.55, normalScale: 0.6, envMapIntensity: 0.35, tintColor: 0x5090cc },  // cool blue — cold air
            'acu-return':  { metalness: 0.4,  roughness: 0.5,  normalScale: 0.7, envMapIntensity: 0.4,  tintColor: 0xc06848 },  // warm coral — hot air
            'custom':      { metalness: 0.15, roughness: 0.75, normalScale: 0,   envMapIntensity: 0.3,  tintColor: null },       // use model color
            'acu-side':    { metalness: 0.0,  roughness: 0.45, normalScale: 0,   envMapIntensity: 0.2,  tintColor: null },       // wall-mounted split unit: moulded plastic housing, model color
            'server-front-photo': { metalness: 0.3,  roughness: 0.6,  normalScale: 0, envMapIntensity: 0.3,  tintColor: 0xffffff },
            'server-rear-socket': { metalness: 0.2,  roughness: 0.65, normalScale: 0, envMapIntensity: 0.25, tintColor: 0xffffff },
            'server-outlet-mesh': { metalness: 0.35, roughness: 0.5,  normalScale: 0, envMapIntensity: 0.3,  tintColor: 0xffffff },
            'rack-strip-left':    { metalness: 0.15, roughness: 0.7,  normalScale: 0, envMapIntensity: 0.2,  tintColor: 0xffffff },
            'rack-strip-right':   { metalness: 0.15, roughness: 0.7,  normalScale: 0, envMapIntensity: 0.2,  tintColor: 0xffffff },
            'acu-return-photo':   { metalness: 0.1,  roughness: 0.6,  normalScale: 0, envMapIntensity: 0.2,  tintColor: 0xffffff },
            'baffle-grille':      _baffleMaterials().preset('baffle-grille'),
        };

            const externalTextureRegistry = {
            'rack-side': { diffuse: null, normal: null, roughness: null, metalness: null },
            'rack-front': { diffuse: null, normal: null, roughness: null, metalness: null },
            'rack-top': { diffuse: null, normal: null, roughness: null, metalness: null },
            'rack-bottom': { diffuse: null, normal: null, roughness: null, metalness: null },
            'server-front': { diffuse: null, normal: null, roughness: null, metalness: null },
            'blank-panel': { diffuse: null, normal: null, roughness: null, metalness: null },
            'floor': { diffuse: null, normal: null, roughness: null, metalness: null },
            'wall': { diffuse: null, normal: null, roughness: null, metalness: null },
            'baffle': { diffuse: null, normal: null, roughness: null, metalness: null },
            'box': { diffuse: null, normal: null, roughness: null, metalness: null },
            'floor-vent': { diffuse: null, normal: null, roughness: null, metalness: null },
            'acu-supply': { diffuse: null, normal: null, roughness: null, metalness: null },
            'acu-return': { diffuse: null, normal: null, roughness: null, metalness: null },
            'server-front-photo': { diffuse: null, normal: null, roughness: null, metalness: null },
            'server-rear-socket': { diffuse: null, normal: null, roughness: null, metalness: null },
            'server-outlet-mesh': { diffuse: null, normal: null, roughness: null, metalness: null },
            'rack-strip-left': { diffuse: null, normal: null, roughness: null, metalness: null },
            'rack-strip-right': { diffuse: null, normal: null, roughness: null, metalness: null },
            'acu-return-photo': { diffuse: null, normal: null, roughness: null, metalness: null },
            'baffle-grille': { diffuse: null, normal: null, roughness: null, metalness: null },
        };

            const _partitionWallProxyMaterialCache = new Map();

            // The debounce handle for external-texture rebuilds.
            //
            // Lives HERE, with the module's other private state, because that is
            // what it is. It was left behind in STL_generate.html (:2115) when
            // these 51 functions were lifted out, so `clearTimeout(...)` below
            // read a free variable that exists only in the legacy page. The HTML
            // app therefore worked and React threw
            // `_textureRebuildTimer is not defined` on EVERY external texture
            // that finished loading — a page error per texture, and the
            // debounced rebuild that was supposed to follow never ran.
            let _textureRebuildTimer = null;
            const _partitionWallBodyGeometryCache = new Map();

            var SERVER_PATCH_TINT = '#ffcc33';

            var STL_EXPORT_GROUPS = [
            // Scene-merged files (single feature level per file; per-solid surface via regions)
            { key: 'rack_frame', filename: 'merged_rack_frame.stl',   kinds: ['rack'], frameOnly: true },
            { key: 'acu',        filename: 'merged_acu.stl',          kinds: ['acu'] },
            { key: 'baffle',     filename: 'merged_baffle.stl',       kinds: ['baffle'] },
            { key: 'floor',      filename: 'merged_floor_ceiling.stl',kinds: ['floor'] },
            { key: 'perforated', filename: 'merged_perforated.stl',   kinds: ['floor'], perforatedOnly: true },
            { key: 'box',        filename: 'merged_box.stl',          kinds: ['box', 'cdu'] },
            { key: 'wall',       filename: 'room_wall.stl',           kinds: ['wall'] },
            { key: 'partitionWall', filename: 'merged_partition_wall.stl', kinds: ['partitionWall'] },
            // Air-side pipes / ducts: closed swept solids, meshed as walls.
            { key: 'pipe',       filename: 'merged_pipe.stl',          kinds: ['pipe'] },
        ];

        
        let activeSourceEmission = null;
        function recordSourceEmission(model, text, operation) {
            if (!activeSourceEmission || !text) return text;
            const uid = model && model.uid;
            if (!uid || !_allModels().some(source => source === model && source.uid === uid))
                throw new Error('STL source region has no current OO entity');
            const regions = [...text.matchAll(/^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?/gm)];
            for (const match of regions) {
                const solid = match[1].trim();
                if (solid !== match[3].trim()) throw new Error('STL source region has inconsistent delimiters');
                const facetCount = (match[2].match(/\bfacet[ \t]+normal\b/g) || []).length;
                if (!facetCount) continue;
                const key = uid + ':' + solid;
                activeSourceEmission.set(key, Object.freeze({sourceEntityUid:uid,
                    solidName:solid, operation, facetCount, block:match[0], sourceEntitySnapshot:JSON.stringify(model)}));
            }
            return text;
        }

        function buildGroupedStlMap(modelIds, includeLeakage) {
            // Returns { filename: stl_content } for each non-empty group.
            // Handles disabled models that are explicitly included in modelIds.
            const idSet = new Set(modelIds);
            const isMirrored = ctx.wrapperGroup.matrixWorld.determinant() < 0;
            const disabledExtras = _allModels().filter(m => idSet.has(m.uid) && !isModelEnabled(m));
            const result = {};
            const emissions = new Map();
            const previousEmission = activeSourceEmission;
            activeSourceEmission = emissions;
            try {
            withExtraModelsRendered(disabledExtras, () => {
                _withUnmergedModels(() => {
                // Cache rack STL strings (with orientation overrides applied) so each rack
                // model is exported exactly once, shared by the frameOnly branch and the
                // per-bucket pass below.
                console.time('[STLExport] rackStlCache');
                const rackStlCache = new Map();
                _allModels().forEach(function(model) {
                    if (model.kind !== 'rack') return;
                    if (!idSet.has(model.uid)) return;
                    if (!isModelEnabled(model) && !disabledExtras.includes(model)) return;
                    var stlRaw = exportModelToStl(model, isMirrored);
                    rackStlCache.set(model.uid, recordSourceEmission(model, applyModelSolidOrientationOverrides(model, stlRaw), 'modelBoundary'));
                });
                console.timeEnd('[STLExport] rackStlCache');
        
                console.time('[STLExport] groups');
                STL_EXPORT_GROUPS.forEach(group => {
                    const groupParts = [];
                    _allModels().forEach(model => {
                        if (!idSet.has(model.uid)) return;
                        if (!group.kinds.includes(model.kind)) return;
                        if (group.frameOnly) {
                            if (model.kind === 'rack') {
                                var rackStl = rackStlCache.get(model.uid);
                                var frameRe = /solid\s+(\S+)\n[\s\S]*?endsolid\s+\S+\n/g;
                                var fmatch;
                                while ((fmatch = frameRe.exec(rackStl)) !== null) {
                                    var sn = fmatch[1];
                                    if (sn.indexOf('_frame_') !== -1 || /_frame$/.test(sn)) {
                                        groupParts.push(fmatch[0]);
                                    }
                                }
                            }
                        } else if (group.perforatedOnly) {
                            if (model.kind === 'floor') {
                                const perfPatchIds = new Set(((model.geometry && model.geometry.openings) || [])
                                    .filter(op => op.type === 'perforated')
                                    .map(op => op.patchId));
                                if (perfPatchIds.size > 0) {
                                    const modelStl = recordSourceEmission(model, applyModelSolidOrientationOverrides(model, exportModelToStl(model, isMirrored)), 'modelBoundary');
                                    const perfSolidNames = new Set();
                                    Object.values(model.geometry.faces).forEach(face => {
                                        (face.patches || []).forEach(patch => {
                                            if (perfPatchIds.has(patch.id)) {
                                                perfSolidNames.add(getPatchFinalExportName(model, face.id || Object.keys(model.geometry.faces).find(k => model.geometry.faces[k] === face), patch));
                                            }
                                        });
                                    });
                                    const solidRegex = /solid\s+(\S+)\n[\s\S]*?endsolid\s+\S+\n/g;
                                    let match;
                                    while ((match = solidRegex.exec(modelStl)) !== null) {
                                        if (perfSolidNames.has(match[1])) groupParts.push(match[0]);
                                    }
                                }
                            }
                        } else {
                            let modelStl = exportModelToStl(model, isMirrored);
                            if (model.kind === 'floor') {
                                const perfPatchIds = new Set(((model.geometry && model.geometry.openings) || [])
                                    .filter(op => op.type === 'perforated')
                                    .map(op => op.patchId));
                                if (perfPatchIds.size > 0) {
                                    const perfSolidNames = new Set();
                                    Object.values(model.geometry.faces).forEach(face => {
                                        (face.patches || []).forEach(patch => {
                                            if (perfPatchIds.has(patch.id)) {
                                                perfSolidNames.add(getPatchFinalExportName(model, face.id || Object.keys(model.geometry.faces).find(k => model.geometry.faces[k] === face), patch));
                                            }
                                        });
                                    });
                                    perfSolidNames.forEach(name => {
                                        const re = new RegExp(`solid\\s+${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\n[\\s\\S]*?endsolid\\s+${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\n`, 'g');
                                        modelStl = modelStl.replace(re, '');
                                    });
                                }
                            }
                            groupParts.push(recordSourceEmission(model, applyModelSolidOrientationOverrides(model, modelStl), 'modelBoundary'));
                        }
                    });
                    const groupStl = groupParts.join('');
                    if (groupStl.trim()) result[group.filename] = groupStl;
                });
                console.timeEnd('[STLExport] groups');
        
                // 2026-04-17 redesign: rack-internal per-bucket STLs.
                console.time('[STLExport] buckets');
                const bucketParts = {};
                _allModels().forEach(function(model) {
                    if (!idSet.has(model.uid) || model.kind !== 'rack') return;
                    if (!isModelEnabled(model) && !disabledExtras.includes(model)) return;
                    var rackStl = rackStlCache.get(model.uid);
                    if (!rackStl) return;
                    var solidRe = /solid\s+(\S+)\n[\s\S]*?endsolid\s+\S+\n/g;
                    var match;
                    while ((match = solidRe.exec(rackStl)) !== null) {
                        var solidName = match[1];
                        var fullBlock = match[0];
                        var target = _classifyRackSolidToBucketFile(solidName, model);
                        if (!target) continue;
                        if (!bucketParts[target]) bucketParts[target] = [];
                        bucketParts[target].push(fullBlock);
                    }
                });
                Object.keys(bucketParts).forEach(fn => { result[fn] = bucketParts[fn].join(''); });
        
                if (includeLeakage) {
                    _allModels().forEach(function(model) {
                        if (!idSet.has(model.uid) || model.kind !== 'rack') return;
                        var leakResult = buildThroughZoneStl(model);
                        if (leakResult && leakResult.stl) recordSourceEmission(model, leakResult.stl, 'cellZone');
                        if (!leakResult || !leakResult.stl) return;
                        var groups = getRackThroughZoneGroups(model);
                        var rName = sanitizeName(model.meta.name);
                        groups.forEach(function(g) {
                            var bucket = g.minSideSpan;
                            var solidName = rName + '_u' + (g.startUnit + 1) + '_u' + (g.endUnit + 1) + '_through_zone';
                            var fn = 'merged_through_zone_' + bucket + 'U__' + solidName + '.stl';
                            var re = new RegExp('solid\\s+' + solidName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\n[\\s\\S]*?endsolid\\s+\\S+\\n');
                            var m = leakResult.stl.match(re);
                            if (m) result[fn] = m[0];
                        });
                    });
                    _allModels().forEach(model => {
                        if (!idSet.has(model.uid) || model.kind !== 'partitionWall') return;
                        const holes = buildPartitionWallHoleZoneStlMap(model);
                        Object.values(holes).forEach(text => recordSourceEmission(model, text, 'cellZone'));
                        Object.assign(result, holes);
                    });
                }
        
                const allDoorParts = [];
                _allModels().forEach(function(model) {
                    if (!idSet.has(model.uid) || model.kind !== 'rack') return;
                    if (!isModelEnabled(model) && !disabledExtras.includes(model)) return;
                    var _faces = Array.isArray(model.geometry.faces) ? model.geometry.faces : [];
                    ['inlet', 'outlet'].forEach(function(side) {
                        var door = _faces.find(function(f){ return f && f.uid === (side + 'Door') && f.role === 'door'; });
                        if (!door || door.renderDoor === false) return;
                        var doorStl = _buildSingleDoorStl(model, side);
                        if (doorStl) allDoorParts.push(recordSourceEmission(model, doorStl, 'doorSheet'));
                    });
                });
                if (allDoorParts.length > 0) {
                    result['merged_rack_door.stl'] = allDoorParts.join('');
                }
                console.timeEnd('[STLExport] buckets');
        
                });
            });
        
            const regions = [];
            Object.keys(result).sort().forEach(filename => {
                const blocks = [...result[filename].matchAll(/^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?/gm)];
                blocks.forEach(match => {
                    const solidName = match[1].trim();
                    if (solidName !== match[3].trim()) throw new Error('STL source region has inconsistent delimiters');
                    const owners = [...emissions.values()].filter(row => row.solidName === solidName && row.block === match[0]);
                    if (owners.length !== 1) throw new Error('STL source region has no unambiguous emitted OO entity: ' + filename + ':' + solidName);
                    regions.push(Object.freeze({...owners[0], filename}));
                });
            });
            groupedSourceCatalogues.set(result, Object.freeze({schema:'dcdtuni.stl-source-emission/v1',
                projectUid:ctx.state.uid, files:Object.freeze({...result}), regions:Object.freeze(regions)}));
            return result;
            } finally { activeSourceEmission = previousEmission; }
        }

        function _buildSingleDoorStl(model, side) {
            var geom = model.geometry;
            var _uid = side + 'Door';
            var door = (Array.isArray(geom.faces) ? geom.faces : []).find(function(f){ return f && f.uid === _uid && f.role === 'door'; });
            if (!door) return '';
            var slotWidth = model.geometry.size.x * geom.slotWidthRatio;
            var slotX = (model.geometry.size.x - slotWidth) / 2;
            var safeBot = model.safeMarginBottom;
            var centralH = geom.slot * geom.slotHeight;
            if (centralH <= 1e-6) return '';
            var zPos = side === 'inlet' ? 0 : model.geometry.size.z;
            var rName = sanitizeName(model.meta.name);
            var solidName = rName + '_' + side + '_door';
            var nz = side === 'inlet' ? -1 : 1;
            var x0 = slotX, x1 = slotX + slotWidth;
            var y0 = safeBot, y1 = safeBot + centralH;
            var mat = new THREE.Matrix4();
            var modelGroup = getRenderRootByModelId(model.uid);
            if (modelGroup) {
                mat.copy(modelGroup.matrixWorld);
            } else {
                mat.makeTranslation(model.geometry.location.x, model.geometry.location.y, model.geometry.location.z);
            }
            // Model-local -> world through THIS door's matrix. (geometryPure once
            // exposed a `tx` that closed over a `mat` it no longer had after the
            // module split, so every perforated-door export threw
            // "mat is not defined".)
            function tx(x, y, z) {
                var v = new THREE.Vector3(x, y, z).applyMatrix4(mat);
                return [v.x, v.y, v.z];
            }
            var p0 = tx(x0, y0, zPos), p1 = tx(x1, y0, zPos),
                p2 = tx(x1, y1, zPos), p3 = tx(x0, y1, zPos);
            var e1 = [p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]];
            var e2 = [p3[0]-p0[0], p3[1]-p0[1], p3[2]-p0[2]];
            var n = [e1[1]*e2[2]-e1[2]*e2[1], e1[2]*e2[0]-e1[0]*e2[2], e1[0]*e2[1]-e1[1]*e2[0]];
            var nLen = Math.sqrt(n[0]*n[0] + n[1]*n[1] + n[2]*n[2]);
            if (nLen > 1e-10) { n[0]/=nLen; n[1]/=nLen; n[2]/=nLen; }
            if (n[2] * nz < 0) { n[0]=-n[0]; n[1]=-n[1]; n[2]=-n[2]; }
            var ns = n[0] + ' ' + n[1] + ' ' + n[2];
            var stl = 'solid ' + solidName + '\n';
            stl += 'facet normal ' + ns + '\n outer loop\n';
            stl += '  vertex ' + p0.join(' ') + '\n  vertex ' + p1.join(' ') + '\n  vertex ' + p2.join(' ') + '\n';
            stl += ' endloop\nendfacet\n';
            stl += 'facet normal ' + ns + '\n outer loop\n';
            stl += '  vertex ' + p0.join(' ') + '\n  vertex ' + p2.join(' ') + '\n  vertex ' + p3.join(' ') + '\n';
            stl += ' endloop\nendfacet\n';
            stl += 'endsolid ' + solidName + '\n';
            return stl;
        }

        
        function _withUnmergedModels(callback) {
            // Temporarily unmerge all model groups so that individual meshes
            // (with their exportName userData) are available for STL export.
            // Re-merges after the callback completes.
            const mergedRoots = ctx.renderRoots.filter(r => r.userData._origChildren);
            mergedRoots.forEach(r => _unmergeModelGroup(r));
            try {
                callback();
            } finally {
                mergedRoots.forEach(r => {
                    r.updateMatrixWorld(true);
                    _mergeModelGroup(r);
                });
            }
        }

        
        function applyModelSolidOrientationOverrides(model, stlText) {
            if (!stlText || !stlText.trim() || !model) return stlText;
            ensurePreviewVisibilityState();
            const reversedRegions = ctx.uiState.previewRegionReverse[model.uid] || {};
            const reversedNames = new Set(Object.keys(reversedRegions).filter(name => reversedRegions[name]));
            const reverseAllSolids = isWallFloorOrientationEligible(model) && isPrimaryFaceSolidNormalReversed(model);
            if (!reverseAllSolids && !reversedNames.size) return stlText;
        
            const output = [];
            let currentSolidName = null;
            let reverseCurrentSolid = false;
            let facetVertices = [];
        
            const flushFacetVertices = () => {
                if (!facetVertices.length) return;
                if (reverseCurrentSolid && facetVertices.length === 3) output.push(facetVertices[0], facetVertices[2], facetVertices[1]);
                else output.push(...facetVertices);
                facetVertices = [];
            };
        
            stlText.split('\n').forEach(line => {
                const trimmed = line.trim();
                const solidMatch = trimmed.match(/^solid\s*(.*)$/i);
                if (solidMatch) {
                    flushFacetVertices();
                    currentSolidName = (solidMatch[1] || '').trim();
                    reverseCurrentSolid = reverseAllSolids || reversedNames.has(currentSolidName);
                    output.push(line);
                    return;
                }
                if (/^endsolid\b/i.test(trimmed)) {
                    flushFacetVertices();
                    currentSolidName = null;
                    reverseCurrentSolid = false;
                    output.push(line);
                    return;
                }
                const normalMatch = trimmed.match(/^facet normal\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)$/i);
                if (reverseCurrentSolid && normalMatch) {
                    const nx = -parseFloat(normalMatch[1]);
                    const ny = -parseFloat(normalMatch[2]);
                    const nz = -parseFloat(normalMatch[3]);
                    const indent = line.match(/^\s*/)?.[0] || '';
                    output.push(`${indent}facet normal ${nx.toFixed(6)} ${ny.toFixed(6)} ${nz.toFixed(6)}`);
                    return;
                }
                if (reverseCurrentSolid && /^vertex\b/i.test(trimmed)) {
                    facetVertices.push(line);
                    if (facetVertices.length === 3) flushFacetVertices();
                    return;
                }
                output.push(line);
            });
        
            flushFacetVertices();
            return output.join('\n');
        }

        
        function buildPartitionWallHoleZoneStlMap(model) {
            if (!model || model.kind !== 'partitionWall') return {};
            const result = {};
            const patchPhysics = cfdprojStateV4.patchPhysicsMap(model);
            const root = getRenderRootByModelId(model.uid);
            let tempRoot = null;
            let targetParent = root;
        
            if (!targetParent) {
                tempRoot = new THREE.Group();
                tempRoot.position.set(model.geometry.location.x, model.geometry.location.y, model.geometry.location.z);
                applyModelTransformToObject(tempRoot, model);
                ctx.globalGroup.add(tempRoot);
                targetParent = tempRoot;
            }
            targetParent.updateMatrixWorld(true);
        
            try {
                getPartitionWallPatchStore(model).forEach((patch, index) => {
                    const phys = patchPhysics[patch.id] || { leakageRate: 100 };
                    const leakageRate = phys.leakageRate != null ? phys.leakageRate : 100;
                    // 2026-04-16 simplification: gate on leakageRate only.
                    if (leakageRate >= 100) return;
        
                    const zoneName = sanitizeName(`${model.meta.name}_hole_${index + 1}_zone`);
                    const zoneGeom = buildPartitionWallHoleZoneGeometry(model, patch);
                    let zoneMaterial = null;
                    let ownsZoneMaterial = false;
                    if (zoneGeom && typeof zoneGeom.clearGroups === 'function') {
                        zoneGeom.clearGroups();
                    }
                    if (zoneGeom && typeof zoneGeom.addGroup === 'function') {
                        zoneGeom.addGroup(0, Infinity, 0);
                    }
                    if (typeof getPartitionWallProxyMaterial === 'function') {
                        zoneMaterial = getPartitionWallProxyMaterial('#c7cdd6', 0.2);
                    }
                    if (!zoneMaterial) {
                        zoneMaterial = new THREE.MeshBasicMaterial({ color: '#c7cdd6' });
                        ownsZoneMaterial = true;
                    }
                    const zoneMesh = new THREE.Mesh(zoneGeom, zoneMaterial);
                    try {
                        targetParent.add(zoneMesh);
                        targetParent.updateMatrixWorld(true);
                        const holeParts = [];
                        exportToStlString(zoneName, zoneMesh, holeParts, false, null);
                        result[`partition_hole_${zoneName}.stl`] = holeParts.join('');
                    } finally {
                        targetParent.remove(zoneMesh);
                        if (zoneMesh.geometry && typeof zoneMesh.geometry.dispose === 'function') {
                            zoneMesh.geometry.dispose();
                        }
                        if (ownsZoneMaterial && zoneMesh.material && typeof zoneMesh.material.dispose === 'function') {
                            zoneMesh.material.dispose();
                        }
                    }
                });
            } finally {
                if (tempRoot) {
                    ctx.globalGroup.remove(tempRoot);
                    disposeRenderObject(tempRoot);
                }
            }
        
            return result;
        }

        
        function buildThroughZoneStl(model) {
            if (model.kind !== 'rack') return null;
            const groups = getRackThroughZoneGroups(model);
            if (!groups.length) return null;
            const unitH = model.geometry.slotHeight;
            const slotW = model.geometry.size.x * model.geometry.slotWidthRatio;
            // 2026-04-19 Y-up: through-cavity spans full rack depth along Z (was Y),
            // vertical along Y between slot bounds (was Z).
            const offsetX = (model.geometry.size.x - slotW) / 2;
            const rackName = sanitizeName(model.meta.name);
            const throughSlots = groups.flatMap(g => { const s = []; for (let i = g.startUnit; i <= g.endUnit; i++) s.push(i); return s; });
            const ranges = groups.map(g => [g.startUnit, g.endUnit]);
            const worldMatrix = getModelWorldMatrix(model);
            const transformVertex = ([x, y, z]) => new THREE.Vector3(x, y, z).applyMatrix4(worldMatrix);
            const appendFacet = (v0, v1, v2) => {
                const normal = new THREE.Vector3()
                    .crossVectors(
                        new THREE.Vector3().subVectors(v1, v0),
                        new THREE.Vector3().subVectors(v2, v0)
                    )
                    .normalize();
                if (!Number.isFinite(normal.x) || !Number.isFinite(normal.y) || !Number.isFinite(normal.z)) return '';
                return `  facet normal ${normal.x.toFixed(6)} ${normal.y.toFixed(6)} ${normal.z.toFixed(6)}\n`
                    + `    outer loop\n`
                    + `      vertex ${v0.x.toFixed(6)} ${v0.y.toFixed(6)} ${v0.z.toFixed(6)}\n`
                    + `      vertex ${v1.x.toFixed(6)} ${v1.y.toFixed(6)} ${v1.z.toFixed(6)}\n`
                    + `      vertex ${v2.x.toFixed(6)} ${v2.y.toFixed(6)} ${v2.z.toFixed(6)}\n`
                    + `    endloop\n`
                    + `  endfacet\n`;
            };
            let stl = '';
            // Rack U-slots start at safeMarginBottom above anchor (see _buildSingleDoorStl),
            // not at y=0. Missing this offsets the through-zone downward by 1 safety
            // margin (typically 1U), clipping the top unit off the top of the zone.
            const safeBot = model.safeMarginBottom;
            groups.forEach(group => {
                const yBot = safeBot + group.startUnit * unitH;
                const yTop = safeBot + (group.endUnit + 1) * unitH;
                const solidName = `${rackName}_u${group.startUnit + 1}_u${group.endUnit + 1}_through_zone`;
                stl += `solid ${solidName}\n`;
                // 2026-04-19 Y-up: W→X (slot width), H→Y (vertical), D→Z (depth).
                const x0 = offsetX, x1 = offsetX + slotW;
                const y0 = yBot, y1 = yTop;
                const z0 = 0, z1 = model.geometry.size.z;
                const faces = [
                    [[x0,y0,z0],[x1,y0,z0],[x1,y0,z1],[x0,y0,z1]],
                    [[x0,y1,z0],[x0,y1,z1],[x1,y1,z1],[x1,y1,z0]],
                    [[x0,y0,z0],[x0,y0,z1],[x0,y1,z1],[x0,y1,z0]],
                    [[x1,y0,z0],[x1,y1,z0],[x1,y1,z1],[x1,y0,z1]],
                    [[x0,y0,z0],[x0,y1,z0],[x1,y1,z0],[x1,y0,z0]],
                    [[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]]
                ];
                faces.forEach(face => {
                    const v = face.map(transformVertex);
                    stl += appendFacet(v[0], v[1], v[2]);
                    stl += appendFacet(v[0], v[2], v[3]);
                });
                stl += `endsolid ${solidName}\n`;
            });
            return { stl, throughSlots, ranges };
        }

        
        function exportModelToStl(model, isMirrored, meshList) {
            const parts = [];
            if (model.kind === 'custom') {
                exportCustomModel(model, parts, isMirrored);
            } else {
                exportBoxModel(model, parts, isMirrored, meshList);
            }
            return parts.join('');
        }

        
        function isModelEnabled(modelOrId) {
            const model = typeof modelOrId === 'string' ? getModelById(modelOrId) : modelOrId;
            if (!model) return false;
            return model.meta.enabled !== false;
        }

        
        function withExtraModelsRendered(extraModels, callback) {
            // Temporarily render disabled models so exportBoxModel can read their geometry.
            // Restores renderMeshes/renderHelpers/renderRoots to original state after callback.
            withTemporaryRenderedModels(extraModels, () => callback());
        }

        
        function getRenderRootByModelId(modelId) {
            return ctx._renderRootMap.get(modelId) || null;
        }

        
        function _mergeModelGroup(modelGroup) {
            if (modelGroup.userData._origChildren) return; // already merged
        
            // 1. Collect all mesh children
            const allMeshes = [];
            // The red anchor ball is a helper, not geometry. Merging would push it
            // into renderMeshes (a hidden ball is still hit, because the raycaster
            // ignores `visible`), and the highlight pass must keep showing, hiding
            // and scaling it as its own object.
            modelGroup.traverse(child => {
                if (child.isMesh && !(child.userData && child.userData.isAnchorMarker)) allMeshes.push(child);
            });
            if (allMeshes.length < 2) return; // nothing worth merging
        
            // 2. Restore any highlight-cloned materials back to shared originals
            //    (updateHighlights clones materials for selected meshes; we must
            //     restore them before grouping by material uuid)
            allMeshes.forEach(mesh => {
                const data = mesh.userData || {};
                if (data._origMaterial) {
                    if (mesh.material !== data._origMaterial) mesh.material.dispose();
                    mesh.material = data._origMaterial;
                    data._origMaterial = null;
                }
            });
        
            // 3. Group by material uuid
            const byMat = {};
            allMeshes.forEach(mesh => {
                const key = mesh.material.uuid;
                if (!byMat[key]) byMat[key] = [];
                byMat[key].push(mesh);
            });
        
            // Check if grouping actually reduces mesh count
            let mergedCount = Object.keys(byMat).length;
            if (mergedCount >= allMeshes.length) return; // no reduction
        
            // 4. Save originals for restore
            const origChildren = allMeshes.map(mesh => ({ mesh, parent: mesh.parent }));
            const origRenderMeshes = allMeshes.filter(m => ctx.renderMeshes.includes(m));
            modelGroup.userData._origChildren = origChildren;
            modelGroup.userData._origRenderMeshes = origRenderMeshes;
        
            // 5. Remove original meshes from parents and renderMeshes
            origChildren.forEach(({ mesh, parent }) => {
                if (parent) parent.remove(mesh);
            });
            // Remove from renderMeshes (backwards to avoid index shift)
            for (let i = ctx.renderMeshes.length - 1; i >= 0; i--) {
                if (origRenderMeshes.includes(ctx.renderMeshes[i])) {
                    ctx.renderMeshes.splice(i, 1);
                }
            }
        
            // 6. Create merged meshes per material group
            const groupInverseMatrix = new THREE.Matrix4().copy(modelGroup.matrixWorld).invert();
            const mergedMeshes = [];
        
            Object.keys(byMat).forEach(matUuid => {
                const meshes = byMat[matUuid];
                if (meshes.length === 1) {
                    // Single mesh in group - just re-add it
                    const mesh = meshes[0];
                    modelGroup.add(mesh);
                    ctx.renderMeshes.push(mesh);
                    mergedMeshes.push(mesh);
                    return;
                }
        
                // Multiple meshes - merge geometries
                try {
                    const tempGeometries = [];
                    meshes.forEach(mesh => {
                        const clonedGeo = mesh.geometry.clone();
                        clonedGeo.applyMatrix4(mesh.matrixWorld);
                        tempGeometries.push(clonedGeo);
                    });
        
                    const mergedGeo = THREE.BufferGeometryUtils.mergeGeometries(tempGeometries, false);
                    if (!mergedGeo) throw new Error('mergeGeometries returned null');
        
                    // Transform back into model-group local space
                    mergedGeo.applyMatrix4(groupInverseMatrix);
        
                    const mergedMesh = new THREE.Mesh(mergedGeo, meshes[0].material);
                    mergedMesh.userData = { isMergedProxy: true, modelId: modelGroup.userData.modelId, origColor: '#' + meshes[0].material.color.getHexString() };
                    modelGroup.add(mergedMesh);
                    ctx.renderMeshes.push(mergedMesh);
                    mergedMeshes.push(mergedMesh);
        
                    // Dispose temp cloned geometries
                    tempGeometries.forEach(g => g.dispose());
                } catch (e) {
                    // Merge failed (incompatible attributes) - fall back to re-adding originals
                    meshes.forEach(mesh => {
                        modelGroup.add(mesh);
                        ctx.renderMeshes.push(mesh);
                        mergedMeshes.push(mesh);
                    });
                }
            });
        
            modelGroup.userData._mergedMeshes = mergedMeshes;
        }

        
        function _unmergeModelGroup(modelGroup) {
            if (!modelGroup.userData._origChildren) return; // not merged
        
            const origChildren = modelGroup.userData._origChildren;
            const mergedMeshes = modelGroup.userData._mergedMeshes || [];
        
            // 1. Remove and dispose merged proxy meshes
            mergedMeshes.forEach(mesh => {
                if (mesh.userData && mesh.userData.isMergedProxy) {
                    if (mesh.parent) mesh.parent.remove(mesh);
                    if (mesh.geometry) mesh.geometry.dispose();
                    // Don't dispose material - it's shared/cached
                    // Remove from renderMeshes
                    const idx = ctx.renderMeshes.indexOf(mesh);
                    if (idx !== -1) ctx.renderMeshes.splice(idx, 1);
                } else {
                    // Non-proxy mesh that was re-added as-is - just remove from renderMeshes
                    const idx = ctx.renderMeshes.indexOf(mesh);
                    if (idx !== -1) ctx.renderMeshes.splice(idx, 1);
                    if (mesh.parent) mesh.parent.remove(mesh);
                }
            });
        
            // 2. Restore original meshes
            origChildren.forEach(({ mesh, parent }) => {
                const target = parent && parent.parent ? parent : modelGroup;
                target.add(mesh);
                ctx.renderMeshes.push(mesh);
            });
        
            // 3. Clear merge data
            delete modelGroup.userData._origChildren;
            delete modelGroup.userData._origRenderMeshes;
            delete modelGroup.userData._mergedMeshes;
        }

        
        function ensurePreviewVisibilityState() {
            if (!ctx.uiState.previewVisibility || typeof ctx.uiState.previewVisibility !== 'object') ctx.uiState.previewVisibility = {};
            if (!ctx.uiState.previewRegionVisibility || typeof ctx.uiState.previewRegionVisibility !== 'object') ctx.uiState.previewRegionVisibility = {};
            if (!ctx.uiState.previewRegionReverse || typeof ctx.uiState.previewRegionReverse !== 'object') ctx.uiState.previewRegionReverse = {};
            _allModels().forEach(model => {
                if (typeof ctx.uiState.previewVisibility[model.uid] !== 'boolean') {
                    ctx.uiState.previewVisibility[model.uid] = model.meta.enabled !== false;
                }
                if (!ctx.uiState.previewRegionVisibility[model.uid] || typeof ctx.uiState.previewRegionVisibility[model.uid] !== 'object') {
                    ctx.uiState.previewRegionVisibility[model.uid] = {};
                }
                if (!ctx.uiState.previewRegionReverse[model.uid] || typeof ctx.uiState.previewRegionReverse[model.uid] !== 'object') {
                    ctx.uiState.previewRegionReverse[model.uid] = {};
                }
            });
        }

        
        function buildPartitionWallHoleZoneGeometry(model, patch) {
            // Zone-defining STL exported to triSurface. The zone box sits
            // strictly inside the wall body so snappy's cellZoneInside inside
            // classifier picks up only cells inside the wall (never fluid cells
            // outside it — the original bug when the CSG cutter's +0.004 m
            // overhang was reused, 2026-04-17).
            //
            // Margin per face is the MIN of
            //     D · 5 %   — physical floor: zone keeps ≥ 90 % of wall thickness
            //                 so the Darcy/Idelchik flow-path length is preserved
            //     h_local · 0.25 — safety against snappy's snap phase snapping
            //                 two near-coplanar surfaces to one; scales with mesh
            // floored at 1 mm absolute so the zone is never coplanar with the
            // wall STL face in the hole's xz footprint.
            //
            // h_local is derived from the saved refinementLevel (falls back to
            // the generic refinementLevel when partitionWallRefinementLevel is
            // absent, which is the frontend's current state — the runner's
            // _preflight_partition_wall_thickness may auto-bump further at mesh
            // time, but the STL is already generated by then, so we use the
            // pre-preflight level as a conservative estimate).
            //
            // IMPORTANT: margin stays below h_local·0.5 so that cell centers of
            // the two innermost cells across the wall (at ±h_local/2) remain
            // INSIDE the zone — otherwise they'd be excluded from the cellZone
            // and the fvOptions porous source would apply to zero cells.
            const cfdRoom = (typeof ctx.state !== 'undefined' && ctx.state && simCfgMerged()) || {};
            const baseSize = Number(cfdRoom.meshBaseSize) || 0.25;
            const rlRaw = cfdRoom.partitionWallRefinementLevel
                       || cfdRoom.refinementLevel;
            const level = Math.max(
                1,
                Array.isArray(rlRaw) ? Math.max(rlRaw[0] || 1, rlRaw[1] || 1) : (Number(rlRaw) || 1)
            );
            const h_local = baseSize / Math.pow(2, level);
            const marginPerFace = Math.max(
                0.001,
                Math.min(model.geometry.size.z * 0.05, h_local * 0.25)
            );
            const depth = model.geometry.size.z - 2 * marginPerFace;
            // 2026-04-18 Y-up: zone thickness along Z, patch.h is face-vertical (Y).
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

        
        function getPartitionWallProxyMaterial(proxyColor, proxyOpacity) {
            const colorKey = new THREE.Color(proxyColor).getHexString();
            const opacityKey = Math.round((proxyOpacity ?? 0) * 1000);
            const cacheKey = `${colorKey}|${opacityKey}`;
            let material = _partitionWallProxyMaterialCache.get(cacheKey);
            if (!material) {
                material = new THREE.MeshPhongMaterial({
                    color: proxyColor,
                    shininess: 20,
                    specular: 0x111111,
                    side: THREE.DoubleSide,
                    transparent: true,
                    opacity: proxyOpacity,
                    depthWrite: false
                });
                material._isCached = true;
                _partitionWallProxyMaterialCache.set(cacheKey, material);
            }
            return material;
        }

        
        function getModelWorldMatrix(model) {
            const tempRoot = new THREE.Group();
            tempRoot.position.set(model.geometry.location.x, model.geometry.location.y, model.geometry.location.z);
            applyModelTransformToObject(tempRoot, model);
            ctx.globalGroup.add(tempRoot);
            tempRoot.updateMatrixWorld(true);
            const worldMatrix = tempRoot.matrixWorld.clone();
            ctx.globalGroup.remove(tempRoot);
            return worldMatrix;
        }

        
        function exportCustomModel(model, parts, isMirrored) {
            const cacheEntry = ctx.geometryCache.get(model.uid);
            if (!cacheEntry) throw new Error(t('error.stl_cache_empty', { name: model.meta.name }));
        
            const tempGroup = new THREE.Group();
            tempGroup.position.set(model.geometry.location.x, model.geometry.location.y, model.geometry.location.z);
            applyModelTransformToObject(tempGroup, model);
            ctx.globalGroup.add(tempGroup);
        
            if (canUseSurfaceProjectionFallback(cacheEntry)) {
                try {
                    exportCustomSurfaceShellModel(model, cacheEntry, parts, isMirrored, tempGroup);
                    ctx.globalGroup.remove(tempGroup);
                    return;
                } catch (fallbackError) {
                    ctx.globalGroup.remove(tempGroup);
                    throw new Error(t('error.surface_shell_export_failed', { error: fallbackError.message }));
                }
            }
        
            const CSG = window.ThreeCSG.CSG;
        
            let baseMesh = new THREE.Mesh(cacheEntry.baseGeometry);
            const _co = model.customMesh ? model.customMesh.offset : { x: 0, y: 0, z: 0 };
            baseMesh.position.set(_co.x, _co.y, _co.z);
            tempGroup.add(baseMesh);
            baseMesh.updateMatrixWorld(true);
            let baseCSG = CSG.fromMesh(baseMesh);
        
            const exportTasks = {};
            Object.keys(model.geometry.faces).forEach(faceId => {
                model.geometry.faces[faceId].patches.forEach(patch => {
                    if (!patch.applied) return;
                    const exportName = getPatchFinalExportName(model, faceId, patch);
                    if (!exportTasks[exportName]) exportTasks[exportName] = [];
                    exportTasks[exportName].push({ faceId, patch });
                });
            });
        
            Object.keys(exportTasks).forEach(name => {
                let unionCutterCSG = null;
                exportTasks[name].forEach(task => {
                    const { cutterGeom } = buildCustomPatchCutterGeometry(model, task.faceId, task.patch);
                    const cutterMesh = new THREE.Mesh(cutterGeom);
                    cutterMesh.updateMatrix();
                    const currentCSG = CSG.fromMesh(cutterMesh);
                    unionCutterCSG = unionCutterCSG ? unionCutterCSG.union(currentCSG) : currentCSG;
                });
        
                if (!unionCutterCSG) return;
                const intersectedCSG = baseCSG.intersect(unionCutterCSG);
                const patchMesh = CSG.toMesh(intersectedCSG, new THREE.Matrix4());
                exportToStlString(name, patchMesh, parts, isMirrored, tempGroup);
                baseCSG = baseCSG.subtract(unionCutterCSG);
            });
        
            const finalBaseMesh = CSG.toMesh(baseCSG, new THREE.Matrix4());
            if (cacheEntry.regions.length > 0) {
                const regionInfos = getRegionReferenceInfos(model, cacheEntry);
                exportMeshByRegions(finalBaseMesh, regionInfos, parts, isMirrored, sanitizeName(model.meta.name), tempGroup);
            } else {
                exportToStlString(sanitizeName(model.meta.name), finalBaseMesh, parts, isMirrored, tempGroup);
            }
            ctx.globalGroup.remove(tempGroup);
        }

        
        function getModelById(modelId) {
            return ctx._modelMap.get(modelId) || null;
        }

        
        function withTemporaryRenderedModels(tempModels, callback) {
            const meshCount0 = ctx.renderMeshes.length;
            const helperCount0 = ctx.renderHelpers.length;
            const rootCount0 = ctx.renderRoots.length;
            const tempRootObjects = [];
        
            tempModels.forEach(model => {
                const modelGroup = new THREE.Group();
                modelGroup.position.set(model.geometry.location.x, model.geometry.location.y, model.geometry.location.z);
                modelGroup.userData = { modelId: model.uid };
                applyModelTransformToObject(modelGroup, model);
                ctx.globalGroup.add(modelGroup);
                ctx.renderRoots.push(modelGroup);
                ctx._renderRootMap.set(model.uid, modelGroup);
                tempRootObjects.push(modelGroup);
        
                const faceLayouts = getFaceLayouts(model);
                const facesIslands = buildFaceIslands(model);
                if (model.kind === 'custom' && ctx.geometryCache.has(model.uid)) renderCustomModel(model, modelGroup, faceLayouts);
                else if (model.kind === 'rack') renderRackModel(model, modelGroup, faceLayouts);
                else if (model.kind === 'wall') renderWallModel(model, modelGroup, faceLayouts);
                else if (model.kind === 'baffle') renderBaffleModel(model, modelGroup, faceLayouts);
                else if (model.kind === 'floor') renderFloorModel(model, modelGroup, faceLayouts);
                else if (model.kind === 'partitionWall') renderPartitionWallModel(model, modelGroup, faceLayouts);
                else if (model.kind === 'pipe') renderPipeModel(model, modelGroup);
                else if (_pure().isWallMountedAcu(model)) renderWallAcuModel(model, modelGroup, faceLayouts, facesIslands);
                else renderBoxModel(model, modelGroup, faceLayouts, facesIslands);
        
                // CRITICAL: update world matrices for this group and all its newly added children.
                // globalGroup.matrixWorld is already correct (from wrapperGroup.updateMatrixWorld called
                // earlier in the export path). modelGroup was just added so its matrixWorld is stale
                // (identity). This call propagates: modelGroup.matrixWorld = globalGroup.matrixWorld × modelGroup.matrix,
                // then recursively updates all child mesh matrices so appendMeshFacets reads correct world positions.
                modelGroup.updateMatrixWorld(true);
            });
        
            const tempMeshes = ctx.renderMeshes.slice(meshCount0);
            try { callback(tempMeshes); }
            finally {
                tempRootObjects.forEach(root => {
                    if (root.parent) root.parent.remove(root);
                    disposeRenderObject(root);
                });
                // Remove temp export roots from map before truncating
                for (let _ri = rootCount0; _ri < ctx.renderRoots.length; _ri++) {
                    const _mid = ctx.renderRoots[_ri].userData && ctx.renderRoots[_ri].userData.modelId;
                    if (_mid) ctx._renderRootMap.delete(_mid);
                }
                ctx.renderRoots.splice(rootCount0);
                ctx.renderMeshes.splice(meshCount0);
                ctx.renderHelpers.splice(helperCount0);
            }
        }

            function simCfgMerged() {
            const r = ctx.state.runDefaults || {};
            return Object.assign({}, r.meshConfig, r.simulationConfig);
        }

        
        function buildCustomPatchCutterGeometry(model, faceId, patch) {
            ensurePatchProjectionDefaults(faceId, patch);
            ensureCustomPatch3DDefaults(model, faceId, patch, patch.regionName || null);
            const { right, up } = getCustomPatchBasis(patch);
            const vector = getPatchVector(faceId, patch);
            const origin = getCustomPatchPosition(patch);
        
            const shape = new THREE.Shape();
            if (patch.type === 'circle') {
                shape.absarc(0, 0, patch.r, 0, Math.PI * 2);
            } else {
                shape.moveTo(-patch.w / 2, -patch.h / 2);
                shape.lineTo(patch.w / 2, -patch.h / 2);
                shape.lineTo(patch.w / 2, patch.h / 2);
                shape.lineTo(-patch.w / 2, patch.h / 2);
                shape.lineTo(-patch.w / 2, -patch.h / 2);
            }
        
            const cutterGeom = new THREE.ExtrudeGeometry(shape, { depth: patch.depth, bevelEnabled: false, curveSegments: 32 });
            cutterGeom.translate(0, 0, -patch.depth * 0.5);
            cutterGeom.applyMatrix4(new THREE.Matrix4().makeBasis(right, up, vector).setPosition(origin));
            return { cutterGeom, vector, origin };
        }

        
        function exportCustomSurfaceShellModel(model, cacheEntry, parts, isMirrored, tempGroup) {
            const allPatches = getAllModelPatches(model);
            cacheEntry.regions.forEach(region => {
                const frame = getPlanarRegionFrame(region, model);
                const basePolys = regionGeometryToPolygons(region, frame, model);
                let remain = basePolys;
        
                allPatches.forEach(({ faceId, patch }) => {
                    if (!patch.applied) return;
                    if (patch.regionName && patch.regionName !== region.name) return;
                    const intersected = intersectPatchBodyWithRegionPlane(model, faceId, patch, frame);
                    if (!intersected) return;
                    let clipped = [];
                    try { clipped = polygonClipping.intersection(basePolys, intersected); } catch (e) { clipped = []; }
                    if (!clipped || !clipped.length) return;
                    exportPlanarPolygons(getPatchFinalExportName(model, faceId, patch), clipped, frame, parts, isMirrored, tempGroup);
                    try { remain = polygonClipping.difference(remain, clipped); } catch (e) {}
                });
        
                if (remain && remain.length) {
                    exportPlanarPolygons(region.name, remain, frame, parts, isMirrored, tempGroup);
                }
            });
        }

        
        function exportMeshByRegions(mesh, regionInfos, parts, isMirrored, fallbackName, targetParent = ctx.globalGroup) {
            const originalParent = mesh.parent;
            const parent = originalParent || targetParent;
            if (!originalParent) parent.add(mesh);
            mesh.updateMatrixWorld(true);
            const mirrored = mesh.matrixWorld.determinant() < 0;
        
            const pos = mesh.geometry.attributes.position;
            const index = mesh.geometry.index;
            const count = index ? index.count : pos.count;
            const grouped = {};
        
            for (let i = 0; i < count; i += 3) {
                let a = index ? index.getX(i) : i;
                let b = index ? index.getX(i + 1) : i + 1;
                let c = index ? index.getX(i + 2) : i + 2;
                if (mirrored) { const tmp = b; b = c; c = tmp; }
        
                const localA = new THREE.Vector3().fromBufferAttribute(pos, a);
                const localB = new THREE.Vector3().fromBufferAttribute(pos, b);
                const localC = new THREE.Vector3().fromBufferAttribute(pos, c);
                const localTri = _getTriangleFacetData(localA, localB, localC);
                if (!localTri) continue;
        
                const localCentroid = new THREE.Vector3().addVectors(localA, localB).add(localC).multiplyScalar(1 / 3);
                const regionName = classifyTriangleRegion(localCentroid, localTri.normal, regionInfos, fallbackName);
                const vA = localA.clone().applyMatrix4(mesh.matrixWorld);
                const vB = localB.clone().applyMatrix4(mesh.matrixWorld);
                const vC = localC.clone().applyMatrix4(mesh.matrixWorld);
                const tri = _getTriangleFacetData(vA, vB, vC);
                if (!tri) continue;
                if (!grouped[regionName]) grouped[regionName] = [];
                grouped[regionName].push(tri);
            }
        
            if (!originalParent) parent.remove(mesh);
        
            Object.keys(grouped).forEach(name => {
                parts.push(`solid ${name}\n`);
                grouped[name].forEach(tri => {
                    parts.push(`  facet normal ${tri.normal.x.toFixed(6)} ${tri.normal.y.toFixed(6)} ${tri.normal.z.toFixed(6)}\n    outer loop\n      vertex ${tri.vA.x.toFixed(6)} ${tri.vA.y.toFixed(6)} ${tri.vA.z.toFixed(6)}\n      vertex ${tri.vB.x.toFixed(6)} ${tri.vB.y.toFixed(6)} ${tri.vB.z.toFixed(6)}\n      vertex ${tri.vC.x.toFixed(6)} ${tri.vC.y.toFixed(6)} ${tri.vC.z.toFixed(6)}\n    endloop\n  endfacet\n`);
                });
                parts.push(`endsolid ${name}\n\n`);
            });
        }

        
        function _baffleProducts() {
            const kernel = (typeof window !== 'undefined' && window.baffleGeometryProduct) ||
                (typeof globalThis !== 'undefined' && globalThis.baffleGeometryProduct);
            if (!kernel) throw new Error('geometry_scene.js needs baffle_geometry_product.js loaded first');
            return kernel;
        }
        function _baffleMaterials() {
            const kernel = (typeof window !== 'undefined' && window.baffleMaterialRecipe) ||
                (typeof globalThis !== 'undefined' && globalThis.baffleMaterialRecipe);
            if (!kernel) throw new Error('geometry_scene.js needs baffle_material_recipe.js loaded first');
            return kernel;
        }
        function renderBaffleModel(model, modelGroup, faceLayouts) {
            const product = _baffleProducts().createProduct(model);
            const islands = buildFaceIslands(model).front || [];
            product.parts.forEach(part => {
                const face = model.geometry.faces[part.faceId];
                // Existing carrier normalization stays outside the owned numerical kernel.
                if (part.isBaseWall) ensureWallOpacity(face, part.wallIndex);
                const geom = new THREE.BufferGeometry();
                geom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(part.positions), 3));
                geom.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(part.normals), 3));
                geom.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(part.uvs), 2));
                geom.setIndex(new THREE.BufferAttribute(new part.indices.constructor(part.indices), 1));
                const context = _boxMaterialContext(part.isBaseWall ? 'baffle' : 'baffle-grille');
                if (!part.isBaseWall) context.patchImageSource = patchImageSource(face, islands[part.islandIndex]);
                const appearance = _baffleMaterials().createRecipe(model, part, context);
                spawnRenderable(modelGroup, geom, Object.assign({}, appearance.options, {
                    userData: { modelId: model.uid, faceId: part.faceId,
                        refIds: part.isBaseWall ? null : part.refIds,
                        ...(part.isBaseWall ? { wallIndex: part.wallIndex } : {}),
                        exportName: part.exportName, isBaseWall: part.isBaseWall, geometryPartId: part.partId }
                }));
            });
        }

        
        // ── Air-side pipe / duct ────────────────────────────────────────
        // A closed swept solid along `geometry.waypoints` (model-local, the
        // first one at the anchor). Round: one tube with filleted elbows plus
        // two end caps; rectangular: one box per straight run plus a cube at
        // every bend. Every mesh carries the model's export name, so the STL
        // writer picks it up like any box face, and the whole thing meshes as
        // a wall (merged_pipe.stl).
        function _pipePathPoints(model) {
            const wps = (model.geometry && model.geometry.waypoints) || [];
            return wps.map(p => new THREE.Vector3(p.x || 0, p.y || 0, p.z || 0));
        }
        function _pipeSmoothedPath(points, filletRadius) {
            const path = new THREE.CurvePath();
            if (points.length < 2) return path;
            if (points.length === 2) { path.add(new THREE.LineCurve3(points[0].clone(), points[1].clone())); return path; }
            const fr = filletRadius || 0.03;
            const fillets = [];
            for (let i = 1; i < points.length - 1; i++) {
                const prev = points[i - 1], cur = points[i], next = points[i + 1];
                const dirIn = new THREE.Vector3().subVectors(cur, prev).normalize();
                const dirOut = new THREE.Vector3().subVectors(next, cur).normalize();
                const trim = Math.min(fr, cur.distanceTo(prev) * 0.45, cur.distanceTo(next) * 0.45);
                fillets.push({ arcStart: cur.clone().sub(dirIn.clone().multiplyScalar(trim)), arcEnd: cur.clone().add(dirOut.clone().multiplyScalar(trim)), corner: cur.clone() });
            }
            path.add(new THREE.LineCurve3(points[0].clone(), fillets[0].arcStart));
            for (let j = 0; j < fillets.length; j++) {
                const f = fillets[j];
                const mid = f.arcStart.clone().add(f.arcEnd).multiplyScalar(0.5);
                mid.add(f.corner.clone().sub(mid).multiplyScalar(0.4));
                path.add(new THREE.QuadraticBezierCurve3(f.arcStart, mid, f.arcEnd));
                const lineEnd = (j < fillets.length - 1) ? fillets[j + 1].arcStart : points[points.length - 1].clone();
                path.add(new THREE.LineCurve3(f.arcEnd, lineEnd));
            }
            return path;
        }
        function renderPipeModel(model, modelGroup) {
            const g = model.geometry || {};
            const pts = _pipePathPoints(model);
            if (pts.length < 2) return;
            const shape = g.shape === 'rect' ? 'rect' : 'round';
            const name = sanitizeName(model.meta.name);
            const opacity = (model.meta && model.meta.display && Number.isFinite(model.meta.display.opacity)) ? model.meta.display.opacity : 1;
            // Galvanized sheet-steel ductwork (js/pbr_materials.js). These
            // tubes, cones and boxes have 0..1 UVs, not metres, so each
            // geometry is tiled here and spawnRenderable is told not to.
            const common = { color: '#b8bec6', opacity, doubleSide: false, objectKind: 'air-duct', pbrUvReady: true,
                userData: { modelId: model.uid, faceId: 'body', refIds: null, wallIndex: 0, exportName: name, isBaseWall: true, isPipeBody: true } };
            const lib = _pbrLibrary();
            const joinAt = (end) => (g.joins || []).find(j => j && j.end === end) || null;
            const boreOf = (m) => {
                if (!m || !m.geometry) return 0;
                const mg = m.geometry;
                return mg.shape === 'rect' ? Math.max(Number(mg.widthM) || 0, Number(mg.heightM) || 0) / 2 : (Number(mg.diameterM) || 0.15) / 2;
            };
            if (shape === 'round') {
                const r = Math.max(0.005, (Number(g.diameterM) || 0.15) / 2);
                // A joined end is left OPEN so the two tubes form one solid; where
                // the partner is wider this (narrower) pipe stops short and a
                // reducer cone bridges the last stretch up to the partner's bore.
                const reducers = [];
                const path = pts.map(p => p.clone());
                ['start', 'end'].forEach(end => {
                    const j = joinAt(end);
                    if (!j) return;
                    const other = getModelById(j.pipeUid);
                    const r2 = boreOf(other);
                    if (!other || r2 <= r + 1e-6) return;
                    const endIdx = end === 'start' ? 0 : path.length - 1;
                    const inIdx = end === 'start' ? 1 : path.length - 2;
                    const inward = path[inIdx].clone().sub(path[endIdx]);
                    const segLen = inward.length(); if (segLen < 1e-6) return;
                    inward.normalize();
                    const len = Math.min(segLen * 0.6, Math.max(0.04, (r2 - r) * 2.5));
                    const tip = path[endIdx].clone();
                    path[endIdx] = tip.clone().add(inward.clone().multiplyScalar(len));
                    reducers.push({ tip, inward, len, r2, end });
                });
                const curve = _pipeSmoothedPath(path, r * 2.5);
                const segs = Math.max(12, 24 * (pts.length - 1));
                const tube = new THREE.TubeGeometry(curve, segs, r, 24, false);
                if (lib) lib.tubeUv(tube, curve.getLength(), r, 'galvanized');
                spawnRenderable(modelGroup, tube, Object.assign({}, common, { skipEdges: true }));
                const capAt = (p, dirAway) => {
                    const cap = new THREE.CircleGeometry(r, 24);
                    if (lib) lib.scaleUv(cap, 2 * r / lib.ROLES.galvanized.tileM, 2 * r / lib.ROLES.galvanized.tileM);
                    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), dirAway.clone().normalize());
                    cap.applyQuaternion(q);
                    cap.translate(p.x, p.y, p.z);
                    spawnRenderable(modelGroup, cap, Object.assign({}, common, { skipEdges: true }));
                };
                if (!joinAt('start')) capAt(path[0], path[0].clone().sub(path[1]));
                if (!joinAt('end')) capAt(path[path.length - 1], path[path.length - 1].clone().sub(path[path.length - 2]));
                reducers.forEach(rd => {
                    // CylinderGeometry: radiusTop at +y, radiusBottom at -y; +y points INTO this pipe
                    const cone = new THREE.CylinderGeometry(r, rd.r2, rd.len, 24, 1, true);
                    if (lib) lib.cylinderUv(cone, rd.r2, rd.len, 'galvanized');
                    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), rd.inward);
                    cone.applyQuaternion(q);
                    const c = rd.tip.clone().add(rd.inward.clone().multiplyScalar(rd.len / 2));
                    cone.translate(c.x, c.y, c.z);
                    spawnRenderable(modelGroup, cone, Object.assign({}, common, { skipEdges: true,
                        userData: Object.assign({}, common.userData, { exportName: name + '_reducer_' + rd.end }) }));
                });
            } else {
                const w = Math.max(0.005, Number(g.widthM) || 0.2);
                const h = Math.max(0.005, Number(g.heightM) || 0.2);
                const cube = Math.max(w, h);
                for (let i = 0; i < pts.length - 1; i++) {
                    let a = pts[i].clone(), b = pts[i + 1].clone();
                    const dir = b.clone().sub(a); const full = dir.length(); if (full < 1e-6) continue;
                    dir.normalize();
                    // Runs stop at the bend cubes instead of running through them.
                    if (i > 0) a.add(dir.clone().multiplyScalar(Math.min(cube / 2, full * 0.4)));
                    if (i < pts.length - 2) b.sub(dir.clone().multiplyScalar(Math.min(cube / 2, full * 0.4)));
                    const len = b.distanceTo(a); if (len < 1e-6) continue;
                    const box = new THREE.BoxGeometry(w, h, len);
                    if (lib) lib.boxUv(box, 'galvanized');
                    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir);
                    box.applyQuaternion(q);
                    const c = a.clone().add(b).multiplyScalar(0.5);
                    box.translate(c.x, c.y, c.z);
                    spawnRenderable(modelGroup, box, Object.assign({}, common, {
                        userData: Object.assign({}, common.userData, { exportName: pts.length > 2 ? name + '_run' + (i + 1) : name }) }));
                    if (i > 0) {
                        const joint = new THREE.BoxGeometry(cube, cube, cube);
                        if (lib) lib.boxUv(joint, 'galvanized');
                        joint.translate(pts[i].x, pts[i].y, pts[i].z);
                        spawnRenderable(modelGroup, joint, Object.assign({}, common, {
                            userData: Object.assign({}, common.userData, { exportName: name + '_bend' + i }) }));
                    }
                }
            }
        }
        // ── Wall-mounted ACU ("挂机") ───────────────────────────────────────
        // Four faces: profile-shaped left and right end caps, the flat top,
        // and one continuous FRONT that turns through a quarter circle into
        // the bottom. The front is built as a grid in unrolled (u, v)
        // coordinates whose lines include every rect-patch edge, so each grid
        // cell belongs wholly to one patch or to the wall part and gets that
        // solid's export name — the STL then carries the louvred outlet as a
        // patch on the curved surface. Patch cells carry (0..1) UVs so a
        // preset picture is stretched over the outlet.
        function renderWallAcuModel(model, modelGroup, faceLayouts, facesIslands) {
            const pr = _pure().wallAcuProfile(model);
            const faces = model.geometry.faces || {};
            const opacity = (model.meta && model.meta.display && Number.isFinite(model.meta.display.opacity)) ? model.meta.display.opacity : 1;
            const acuPP = cfdprojStateV4.patchPhysicsMap(model);
            const name = sanitizeName(model.meta.name);
            const wallPartName = (faceId) => composeModelScopedExportName(model, faceId, faces[faceId] || {}, { isWallPart: true, partIndex: 0, partCount: 1 });
            // A wall-mounted unit has no back or bottom skin (they sit on the
            // wall), so its four faces are rendered on both sides: seen from
            // the wall side the curved front would otherwise be culled and
            // the unit looked see-through.
            const common = (faceId, extra) => Object.assign({
                color: '#9ba0a9', opacity, doubleSide: true, objectKind: 'acu-side',
                userData: { modelId: model.uid, faceId, refIds: null, wallIndex: 0, exportName: wallPartName(faceId), isBaseWall: true }
            }, extra || {});

            // end caps: the profile polygon in (z, y), extruded to nothing
            const profilePts = [];
            const nArc = 12;
            profilePts.push([pr.D, 0]);
            for (let i = 0; i <= nArc; i++) {
                const v = pr.flat + pr.arc * i / nArc; const q = pr.at(v); profilePts.push([q.z, q.y]);
            }
            profilePts.push([0, pr.H]); profilePts.push([pr.D, pr.H]);
            const capShape = new THREE.Shape();
            profilePts.forEach((pt, i) => i === 0 ? capShape.moveTo(pt[0], pt[1]) : capShape.lineTo(pt[0], pt[1]));
            // right cap at x = W (normal +x): shape (z, y) -> (x=W, y, z)
            const capR = new THREE.ShapeGeometry(capShape);
            capR.applyMatrix4(new THREE.Matrix4().makeBasis(new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0), new THREE.Vector3(1, 0, 0)).setPosition(pr.W, 0, 0));
            // makeBasis(right, up, normal) with right=+z, up=+y gives normal right×up = (0,0,1)×(0,1,0) = (-1,0,0): flip for +x
            const capRflipped = capR.clone(); { const idx = capRflipped.index; if (idx) { const a = idx.array; for (let k = 0; k < a.length; k += 3) { const t = a[k + 1]; a[k + 1] = a[k + 2]; a[k + 2] = t; } idx.needsUpdate = true; } capRflipped.computeVertexNormals(); }
            spawnRenderable(modelGroup, capRflipped, common('right'));
            const capL = new THREE.ShapeGeometry(capShape);
            capL.applyMatrix4(new THREE.Matrix4().makeBasis(new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0), new THREE.Vector3(-1, 0, 0)).setPosition(0, 0, 0));
            spawnRenderable(modelGroup, capL, common('left'));
            // top: y = H, x 0..W, z 0..D, normal +y
            {
                const g = new THREE.BufferGeometry();
                const v = new Float32Array([0, pr.H, 0,  pr.W, pr.H, 0,  pr.W, pr.H, pr.D,  0, pr.H, pr.D]);
                g.setAttribute('position', new THREE.BufferAttribute(v, 3));
                g.setIndex([0, 2, 1, 0, 3, 2]); g.computeVertexNormals();
                // patches on the top (a return grille): islands rendered as sub-rects
                const topFace = faces.top || {};
                const topIslands = (facesIslands && facesIslands.top) || [];
                if (topIslands.length) {
                    let wallPoly = rectToPoly(0, 0, pr.W, pr.D);
                    topIslands.forEach((island, islandIdx) => {
                        wallPoly = polygonClipping.difference(wallPoly, island.poly);
                        // island.poly is a MultiPolygon: [ polygon ] with polygon = [ outer ring, holes... ]
                        const shape = new THREE.Shape();
                        (island.poly[0] && island.poly[0][0] ? island.poly[0][0] : []).forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                        for (let hi = 1; island.poly[0] && hi < island.poly[0].length; hi++) { const hole = new THREE.Path(); island.poly[0][hi].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1])); shape.holes.push(hole); }
                        const sg = new THREE.ShapeGeometry(shape, 32);
                        sg.applyMatrix4(faceLayouts.top.mat);
                        const role = acuPP[island.refIds[0]] && acuPP[island.refIds[0]].role;
                        const img = patchImageSource(topFace, island);
                        spawnRenderable(modelGroup, sg, {
                            color: img ? '#ffffff' : island.color, opacity, doubleSide: true, renderOrder: 3,
                            objectKind: img ? (FACE_IMAGE_KIND + img) : (role === 'return' ? 'acu-return-photo' : (role === 'supply' ? 'acu-supply' : 'box')),
                            uvNormalize: { type: 'stretch' },
                            userData: { modelId: model.uid, faceId: 'top', refIds: island.refIds, exportName: composeModelScopedExportName(model, 'top', topFace, { isPatch: true, patchIndex: islandIdx, patchCount: topIslands.length }), isBaseWall: false }
                        });
                    });
                    wallPoly.forEach((polygon, wallIndex) => {
                        const shape = new THREE.Shape();
                        polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                        for (let i = 1; i < polygon.length; i++) { const hole = new THREE.Path(); polygon[i].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1])); shape.holes.push(hole); }
                        const sg = new THREE.ShapeGeometry(shape, 32);
                        sg.applyMatrix4(faceLayouts.top.mat);
                        spawnRenderable(modelGroup, sg, common('top', { userData: { modelId: model.uid, faceId: 'top', refIds: null, wallIndex, exportName: composeModelScopedExportName(model, 'top', topFace, { isWallPart: true, partIndex: wallIndex, partCount: wallPoly.length }), isBaseWall: true } }));
                    });
                } else {
                    spawnRenderable(modelGroup, g, common('top'));
                }
            }
            // front: unrolled grid
            const frontFace = faces.front || {};
            const patches = (frontFace.patches || []).filter(p => p && p.type === 'rect');
            const us = new Set([0, pr.W]); const vs = new Set([0, pr.flat, pr.flat + pr.arc, pr.L]);
            for (let i = 1; i < nArc; i++) vs.add(pr.flat + pr.arc * i / nArc);
            patches.forEach(p => { us.add(Math.max(0, Math.min(pr.W, p.x))); us.add(Math.max(0, Math.min(pr.W, p.x + p.w))); vs.add(Math.max(0, Math.min(pr.L, p.y))); vs.add(Math.max(0, Math.min(pr.L, p.y + p.h))); });
            const U = Array.from(us).sort((a, b) => a - b), V = Array.from(vs).sort((a, b) => a - b);
            const groups = {};   // exportName -> {pos:[], uv:[], idx:[], opts}
            const frontIslands = (facesIslands && facesIslands.front) || [];
            const islandOfPatch = (pid) => frontIslands.findIndex(isl => isl.refIds && isl.refIds.indexOf(pid) >= 0);
            const cellOwner = (uc, vc) => {
                for (let k = 0; k < patches.length; k++) {
                    const p = patches[k];
                    if (uc >= p.x && uc <= p.x + p.w && vc >= p.y && vc <= p.y + p.h) return p;
                }
                return null;
            };
            for (let i = 0; i + 1 < U.length; i++) {
                for (let j = 0; j + 1 < V.length; j++) {
                    const u0 = U[i], u1 = U[i + 1], v0 = V[j], v1 = V[j + 1];
                    if (u1 - u0 < 1e-9 || v1 - v0 < 1e-9) continue;
                    const owner = cellOwner((u0 + u1) / 2, (v0 + v1) / 2);
                    let key, opts;
                    if (owner) {
                        const ii = Math.max(0, islandOfPatch(owner.id));
                        key = composeModelScopedExportName(model, 'front', frontFace, { isPatch: true, patchIndex: ii, patchCount: Math.max(1, frontIslands.length) });
                        const role = acuPP[owner.id] && acuPP[owner.id].role;
                        const img = patchImageSource(frontFace, { refIds: [owner.id] });
                        opts = { color: img ? '#ffffff' : (owner.color || '#e06c75'), opacity, doubleSide: true, renderOrder: 3,
                            objectKind: img ? (FACE_IMAGE_KIND + img) : (role === 'supply' ? 'acu-supply' : (role === 'return' ? 'acu-return-photo' : 'box')),
                            userData: { modelId: model.uid, faceId: 'front', refIds: [owner.id], exportName: key, isBaseWall: false, patchRect: owner } };
                    } else {
                        key = wallPartName('front');
                        opts = common('front');
                    }
                    if (!groups[key]) groups[key] = { pos: [], uv: [], idx: [], opts };
                    const gr = groups[key];
                    const base = gr.pos.length / 3;
                    const corners = [[u0, v0], [u1, v0], [u1, v1], [u0, v1]];
                    corners.forEach(([u, v]) => {
                        const pt = _pure().wallAcuPointLocal(pr, u, v);
                        gr.pos.push(pt.x, pt.y, pt.z);
                        if (owner) gr.uv.push((u - owner.x) / owner.w, (v - owner.y) / owner.h); else gr.uv.push(u / pr.W, v / pr.L);
                    });
                    // outward normal: (ny, nz) of the profile; winding chosen so
                    // the face normal (right-hand) points outward
                    gr.idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
                }
            }
            Object.keys(groups).forEach(key => {
                const gr = groups[key];
                const g = new THREE.BufferGeometry();
                g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(gr.pos), 3));
                g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(gr.uv), 2));
                g.setIndex(gr.idx);
                g.computeVertexNormals();
                spawnRenderable(modelGroup, g, Object.assign({}, gr.opts, { skipEdges: false }));
            });
        }

        function _boxMaterialRecipes() {
            var kernel = (typeof window !== 'undefined' && window.boxMaterialRecipe) ||
                (typeof globalThis !== 'undefined' && globalThis.boxMaterialRecipe);
            if (!kernel) throw new Error('geometry_scene.js needs box_material_recipe.js loaded first');
            return kernel;
        }
        function _boxMaterialContext(typeKey) {
            const lib = _pbrLibrary();
            const ext = externalTextureRegistry[typeKey];
            const base = ext && (ext.diffuse || ext.normal);
            const role = lib && lib.ROLES && lib.ROLES['painted-steel'];
            return { libraryAvailable: !!lib,
                ...(role ? { paintedSteelRole: role, meanRoughness: (lib.SETS && lib.SETS[role.set] || {}).meanRoughness || 1 } : {}),
                tileSize: TEXTURE_TILE_SIZES[typeKey], clampToEdge: !!(base && base.wrapS === THREE.ClampToEdgeWrapping) };
        }

        function renderBoxModel(model, modelGroup, faceLayouts, facesIslands) {
            if (model.kind !== 'box' && model.kind !== 'cdu') {
                // Existing ACU/cold-plate/custom fallback callers retain their
                // legacy carrier; they are outside this domain product slice.
                Object.keys(model.geometry.faces).forEach(faceId => {
                    _buildSingleBoxFace(modelGroup, model, faceLayouts, faceId, facesIslands, 1.0);
                });
                return;
            }
            const product = _pure()._boxGeometryProduct(model, model.kind, { faceIslands: facesIslands });
            product.parts.forEach(part => {
                const face = model.geometry.faces[part.faceId];
                // Keep the established document normalization outside the pure core.
                if (part.isBaseWall) ensureWallOpacity(face, part.wallIndex);
                const geom = new THREE.BufferGeometry();
                geom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(part.positions), 3));
                geom.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(part.normals), 3));
                geom.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(part.uvs), 2));
                geom.setIndex(new THREE.BufferAttribute(new part.indices.constructor(part.indices), 1));
                if (part.isBaseWall && model.kind === 'cdu') faceLayouts[part.faceId].color = part.materialRecipe.color;
                // The kernel reads plain source refs; preset lookup and current library
                // capabilities are explicit carrier context, never renderer references.
                const context = _boxMaterialContext(model.kind === 'cdu' ? 'cdu-body' : 'box');
                if (!part.isBaseWall) context.patchImageSource = patchImageSource(face, facesIslands[part.faceId][part.islandIndex]);
                const appearance = _boxMaterialRecipes().createRecipe(model, model.kind, part, context);
                spawnRenderable(modelGroup, geom, Object.assign({}, appearance.options, {
                    userData: { modelId: model.uid, faceId: part.faceId,
                        refIds: part.isBaseWall ? null : part.refIds,
                        ...(part.isBaseWall ? { wallIndex: part.wallIndex } : {}),
                        exportName: part.exportName, isBaseWall: part.isBaseWall, geometryPartId: part.partId }
                }));
            });
        }

        
        function renderCustomModel(model, modelGroup, faceLayouts) {
            const cacheEntry = ctx.geometryCache.get(model.uid);
            if (!cacheEntry) return;
        
            // In liquid mode, dim air models (non-liquid kinds) to indicate read-only state
            const _isLiquidDMC = (typeof isLiquidMode === 'function') && isLiquidMode();
            const _liquidKsC = ['cdu','crh','cold_plate','heat_source','pipe_segment','pipe_junction','rack'];
            const _dimMultC = 1.0;
        
            const regionColors = ['#8a919e', '#7d8794', '#909aa8', '#6f7884'];
            const _co = model.customMesh ? model.customMesh.offset : { x: 0, y: 0, z: 0 };
            if (cacheEntry.regions.length > 0) {
                cacheEntry.regions.forEach((region, index) => {
                    const meshOpacity = (model.meta.display.opacity ?? 1) * _dimMultC;
                    const mesh = new THREE.Mesh(region.geometry.clone(), createMaterialForType('custom', {
                        color: regionColors[index % regionColors.length],
                        doubleSide: true,
                        opacity: meshOpacity,
                        transparent: meshOpacity < 0.999,
                        depthWrite: meshOpacity >= 0.999
                    }));
                    mesh.position.set(_co.x, _co.y, _co.z);
                    mesh.userData = {
                        modelId: model.uid,
                        regionName: region.name,
                        origColor: regionColors[index % regionColors.length],
                        baseOpacity: meshOpacity,
                        exportName: region.name,
                        isImportedBase: true,
                        isImportedRegion: true,
                        isBaseWall: false,
                        _materialPreset: 'custom'
                    };
                    var _customEdgeOp = meshOpacity < 0.05 ? 0 : Math.min(0.18, meshOpacity * meshOpacity * 0.25);
                    if (_customEdgeOp > 0.02) {
                        mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(region.geometry), new THREE.LineBasicMaterial({
                            color: 0x000000,
                            opacity: _customEdgeOp,
                            transparent: true,
                            depthWrite: false
                        })));
                    }
                    modelGroup.add(mesh);
                    ctx.renderMeshes.push(mesh);
                });
            } else {
                const baseOpacity = (model.meta.display.opacity ?? 1) * _dimMultC;
                const baseMesh = new THREE.Mesh(cacheEntry.baseGeometry.clone(), createMaterialForType('custom', {
                    color: '#8a919e',
                    doubleSide: true,
                    opacity: baseOpacity,
                    transparent: baseOpacity < 0.999,
                    depthWrite: baseOpacity >= 0.999
                }));
                baseMesh.position.set(_co.x, _co.y, _co.z);
                baseMesh.userData = {
                    modelId: model.uid,
                    origColor: '#8a919e',
                    baseOpacity,
                    exportName: sanitizeName(model.meta.name),
                    isImportedBase: true,
                    isBaseWall: false,
                    _materialPreset: 'custom'
                };
                modelGroup.add(baseMesh);
                ctx.renderMeshes.push(baseMesh);
            }
        
            getAllModelPatches(model).forEach(({ faceId, patch }) => {
                if (patch.applied && canUseSurfaceProjectionFallback(cacheEntry)) {
                    const fragments = getAppliedCustomPatchFragments(model, faceId, patch);
                    fragments.forEach(fragment => {
                        fragment.polygons.forEach(polygon => {
                            const shape = new THREE.Shape();
                            polygon[0].forEach((pt, index) => index === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                            for (let i = 1; i < polygon.length; i++) {
                                const hole = new THREE.Path();
                                polygon[i].forEach((pt, index) => index === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                                shape.holes.push(hole);
                            }
                            const geom = new THREE.ShapeGeometry(shape, 48);
                            geom.applyMatrix4(fragment.frame.matrix);
                            spawnRenderable(modelGroup, geom, {
                                color: patch.color,
                                opacity: 0.78 * (model.meta.display.opacity ?? 1) * _dimMultC,
                                renderOrder: 3,
                                userData: {
                                    modelId: model.uid,
                                    faceId,
                                    regionName: fragment.regionName,
                                    refIds: [patch.id],
                                    exportName: getPatchFinalExportName(model, faceId, patch),
                                    isBaseWall: false,
                                    isAppliedPatchRegion: true
                                }
                            });
                        });
                    });
                    return;
                }
        
                const { cutterGeom, vector, origin } = buildCustomPatchCutterGeometry(model, faceId, patch);
                spawnRenderable(modelGroup, cutterGeom, {
                    color: patch.color,
                    opacity: 0.45 * (model.meta.display.opacity ?? 1) * _dimMultC,
                    depthWrite: false,
                    userData: {
                        modelId: model.uid,
                        faceId,
                        regionName: patch.regionName || null,
                        refIds: [patch.id],
                        exportName: getPatchFinalExportName(model, faceId, patch),
                        isBaseWall: false,
                        isCustomPatchBody: true
                    }
                });
        
                if (patch.showVector) {
                    const arrow = new THREE.ArrowHelper(vector, origin, Math.max(patch.depth * 0.65, 0.08), 0xffcc66, 0.12, 0.06);
                    arrow.userData = { modelId: model.uid, faceId, refIds: [patch.id], origColor: '#ffcc66' };
                    modelGroup.add(arrow);
                    ctx.renderHelpers.push(arrow);
        
                    // Unit sphere + registry entry (WP-S1): the tip shows at its
                    // data radius — depth-relative, clamped 25-80 mm — at normal
                    // and far distances, and shrinks with the camera up close.
                    // The baseline scale keeps it correct before the first
                    // syncHelperScales frame; the parent-scale division in the
                    // sync covers modelGroup's transform. Unregistration rides
                    // disposeRenderObject, which both apps' clear paths call on
                    // every render root. ArrowHelper head sizes are data-space
                    // and stay untouched.
                    const tipRadius = Math.max(Math.min(patch.depth * 0.08, 0.08), 0.025);
                    const tipGeom = new THREE.SphereGeometry(1, 18, 18);
                    const tipMesh = new THREE.Mesh(tipGeom, new THREE.MeshPhongMaterial({ color: '#ffcc66', shininess: 30, specular: 0x111111 }));
                    tipMesh.position.copy(origin.clone().add(vector.clone().multiplyScalar(Math.max(patch.depth * 0.65, 0.08))));
                    tipMesh.scale.setScalar(tipRadius);
                    tipMesh.userData = {
                        modelId: model.uid,
                        faceId,
                        regionName: patch.regionName || null,
                        refIds: [patch.id],
                        origColor: '#ffcc66',
                        exportName: null,
                        isVectorHandle: true
                    };
                    _pure().registerHelper(tipMesh, tipRadius);
                    modelGroup.add(tipMesh);
                    ctx.renderMeshes.push(tipMesh);
                }
            });
        }

        
        function renderFloorModel(model, modelGroup, faceLayouts) {
            const _fs = _floorBodyStyle(model);
            renderPlanarModelWithPatches(model, modelGroup, faceLayouts, 'top', _fs.color, _fs.kind, 'baffle-grille');
        }

        
        function renderPartitionWallModel(model, modelGroup, faceLayouts) {
            syncPartitionWallModelState(model);
        
            const CSG = window.ThreeCSG && window.ThreeCSG.CSG;
            if (!CSG) {
                throw new Error('Partition wall rendering requires window.ThreeCSG.CSG (vendor/three-csg-ts.umd.js).');
            }
        
            const opacity = Number.isFinite(model.meta.display.opacity) ? model.meta.display.opacity : 1;
            const bodyMaterial = new THREE.MeshPhongMaterial({
                color: '#8a919e',
                shininess: 30,
                specular: 0x111111,
                side: THREE.DoubleSide,
                transparent: opacity < 0.999,
                opacity,
                depthWrite: opacity >= 0.999
            });
        
            const patches = getPartitionWallPatchStore(model);
            const bodySignature = getPartitionWallGeometrySignature(model);
            const cachedBodyEntry = _partitionWallBodyGeometryCache.get(model.uid);
            let finalBodyMesh;
        
            if (cachedBodyEntry && cachedBodyEntry.signature === bodySignature && cachedBodyEntry.geometry) {
                finalBodyMesh = new THREE.Mesh(cachedBodyEntry.geometry.clone(), bodyMaterial);
            } else {
                if (cachedBodyEntry && cachedBodyEntry.geometry) {
                    cachedBodyEntry.geometry.dispose();
                }
                // 2026-04-18 Y-up: W along X, H along Y (vertical), D along Z (thickness).
                const bodyGeom = new THREE.BoxGeometry(model.geometry.size.x, model.geometry.size.y, model.geometry.size.z);
                bodyGeom.translate(model.geometry.size.x * 0.5, model.geometry.size.y * 0.5, model.geometry.size.z * 0.5);
                const bodyMesh = new THREE.Mesh(bodyGeom, bodyMaterial);
                bodyMesh.castShadow = false;
                bodyMesh.receiveShadow = false;
                bodyMesh.userData = {
                    modelId: model.uid,
                    faceId: 'front',
                    exportName: `${sanitizeName(model.meta.name)}_body`,
                    origColor: '#8a919e',
                    isBaseWall: true
                };
                bodyMesh.updateMatrix();
                let bodyCSG = CSG.fromMesh(bodyMesh);
        
                patches.forEach(patch => {
                    const cutterGeom = buildPartitionWallHoleCutterGeometry(model, patch);
                    const cutterMesh = new THREE.Mesh(cutterGeom);
                    cutterMesh.updateMatrix();
                    const cutterCSG = CSG.fromMesh(cutterMesh);
                    bodyCSG = bodyCSG.subtract(cutterCSG);
                    cutterGeom.dispose();
                });
        
                bodyGeom.dispose();
        
                finalBodyMesh = CSG.toMesh(bodyCSG, new THREE.Matrix4());
                _partitionWallBodyGeometryCache.set(model.uid, {
                    signature: bodySignature,
                    geometry: finalBodyMesh.geometry.clone()
                });
                finalBodyMesh.material = bodyMaterial;
            }
            finalBodyMesh.castShadow = false;
            finalBodyMesh.receiveShadow = false;
            finalBodyMesh.userData = {
                modelId: model.uid,
                faceId: 'front',
                exportName: `${sanitizeName(model.meta.name)}_body`,
                origColor: '#8a919e',
                isBaseWall: true
            };
            modelGroup.add(finalBodyMesh);
            ctx.renderMeshes.push(finalBodyMesh);
        
            const proxyOpacity = Math.min(0.35, Math.max(0.08, opacity * 0.25));
            patches.forEach(patch => {
                const proxyGeom = buildPartitionWallHoleProxyGeometry(patch);
                const proxyColor = '#c7cdd6';
                // 2026-04-18 Y-up: patch-local y is world Y (vertical); front/back
                // faces sit at Z=0 / Z=D, so proxies float just in Z.
                const centerX = patch.type === 'circle' ? patch.cx : patch.x + patch.w * 0.5;
                const centerY = patch.type === 'circle' ? patch.cy : patch.y + patch.h * 0.5;
        
                const proxyMaterial = getPartitionWallProxyMaterial(proxyColor, proxyOpacity);
                const frontProxy = new THREE.Mesh(proxyGeom.clone(), proxyMaterial);
                frontProxy.position.set(centerX, centerY, -0.001);
                frontProxy.castShadow = false;
                frontProxy.receiveShadow = false;
                frontProxy.renderOrder = 6;
                frontProxy.userData = {
                    modelId: model.uid,
                    faceId: 'front',
                    refIds: [patch.id],
                    origColor: proxyColor,
                    isPartitionWallHoleProxy: true,
                    exportName: null
                };
                modelGroup.add(frontProxy);
                ctx.renderMeshes.push(frontProxy);
        
                const backProxy = new THREE.Mesh(proxyGeom.clone(), proxyMaterial);
                backProxy.position.set(centerX, centerY, model.geometry.size.z + 0.001);
                backProxy.castShadow = false;
                backProxy.receiveShadow = false;
                backProxy.renderOrder = 6;
                backProxy.userData = {
                    modelId: model.uid,
                    faceId: 'back',
                    refIds: [patch.id],
                    origColor: proxyColor,
                    isPartitionWallHoleProxy: true,
                    exportName: null
                };
                modelGroup.add(backProxy);
                ctx.renderMeshes.push(backProxy);
        
                proxyGeom.dispose();
            });
        
        }

        
        function renderRackModel(model, modelGroup, faceLayouts) {
            // In liquid mode, dim air models (non-liquid kinds) to indicate read-only state
            const _isLiquidDM = (typeof isLiquidMode === 'function') && isLiquidMode();
            const _liquidKs = ['cdu','crh','cold_plate','heat_source','pipe_segment','pipe_junction','rack'];
            const _dimMult = 1.0;
            const geom = model.geometry;
            const opacity = (model.meta.display.opacity ?? 1) * _dimMult;
            const slotWidth = geom.size.x * geom.slotWidthRatio;
            const slotX = (geom.size.x - slotWidth) / 2;
            const safeBottomHeight = model.safeMarginBottom;
            const safeTopHeight = model.safeMarginTop;
            const slotAreaHeight = geom.slot * geom.slotHeight;
            const centralFrameHeight = slotAreaHeight;
            const channelWallColor = '#707783';
        
            const rackFaceKind = { left: 'rack-side', right: 'rack-side', top: 'rack-top', bottom: 'rack-bottom', front: 'rack-front', back: 'rack-front' };
            const spawnFaceRect = (faceId, x, y, width, height, color, userData, objectKindOverride) => {
                const layout = faceLayouts[faceId];
                const faceOpacity = (model.geometry.faces[faceId]?.opacity ?? (model.meta.display.opacity ?? 1)) * _dimMult;
                return spawnPlanarRect(modelGroup, layout.origin.clone().add(layout.right.clone().multiplyScalar(x)).add(layout.up.clone().multiplyScalar(y)), layout.right.clone(), layout.up.clone(), width, height, {
                    color,
                    opacity: faceOpacity,
                    objectKind: objectKindOverride || rackFaceKind[faceId] || 'rack-side',
                    userData: { modelId: model.uid, faceId, ...userData }
                });
            };
        
            const rName = sanitizeName(model.meta.name);
            var inletOff = model.inletOffset || 0;
            var outletOff = model.outletOffset || 0;
            var slotDepthStart = inletOff;
            var slotDepthEnd = model.geometry.size.z - outletOff;
            // --- Frame geometry (outer shell + margin strips + inner walls) ---
            buildRackFrameGeometry(modelGroup, model, faceLayouts, {
                rName: rName,
                W: model.geometry.size.x, D: model.geometry.size.z, H: model.geometry.size.y,
                slotX: slotX, slotWidth: slotWidth,
                slotAreaHeight: slotAreaHeight,
                marginBottom: model.safeMarginBottom,
                marginTop: model.safeMarginTop,
                slotDepthStart: slotDepthStart, slotDepthEnd: slotDepthEnd,
                inletOff: inletOff, outletOff: outletOff,
                opacity: opacity, dimMult: _dimMult
            });
        
            ['front', 'back'].forEach(side => {
                const frameColor = side === 'front' ? '#5c6370' : '#707783';
        
                // Merged contiguous empty slot ranges instead of individual per-slot walls
                // Ranges are split at opposite-void boundaries; half-dissolved ranges get wallBaffle suffix
                const mergedRanges = getRackMergedEmptySlotRanges(model, side);
                mergedRanges.forEach(range => {
                    const startBounds = getRackSlotBounds(model, range.startUnit);
                    const endBounds = getRackSlotBounds(model, range.endUnit);
                    const mergedY = startBounds.y;
                    const mergedH = (endBounds.y + endBounds.h) - startBounds.y;
                    const baseName = getRackMergedSlotExportName(model, side, range.startUnit, range.endUnit);
                    const exportName = baseName;
                    // Wall-baffle faces are colored slightly differently to hint at their special treatment
                    const faceColor = range.isWallBaffle ? '#b07040' : frameColor;
                    // When depth offset is set, blind panels move inward to match equipment faces
                    const _blindOff = side === 'front' ? inletOff : outletOff;
                    if (_blindOff > 1e-6) {
                        // 2026-04-18 Y-up: depth offset along Z, vertical position along Y.
                        const _blindZ = side === 'front' ? slotDepthStart : slotDepthEnd;
                        const _blindRight = side === 'front' ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(-1, 0, 0);
                        const _blindOriginX = side === 'front' ? startBounds.x : startBounds.x + startBounds.w;
                        spawnPlanarRect(modelGroup,
                            new THREE.Vector3(_blindOriginX, mergedY, _blindZ),
                            _blindRight, new THREE.Vector3(0, 1, 0),
                            startBounds.w, mergedH,
                            { color: faceColor, opacity, objectKind: 'blank-panel',
                              userData: { modelId: model.uid, faceId: side, isBaseWall: true, isWallBaffle: range.isWallBaffle, side, exportName } }
                        );
                    } else {
                        spawnFaceRect(side, startBounds.x, mergedY, startBounds.w, mergedH, faceColor, {
                            isBaseWall: true,
                            isWallBaffle: range.isWallBaffle,
                            side,
                            exportName
                        }, (side === 'front' || side === 'back') ? 'blank-panel' : undefined);
                    }
                    // Draw visual u-slot boundary lines within merged ranges
                    if (range.startUnit < range.endUnit) {
                        const layout = faceLayouts[side];
                        const faceNormal = new THREE.Vector3().crossVectors(layout.right, layout.up).normalize();
                        // Determine if lines need depth offset to follow recessed slot face
                        const _lineBlindOff = side === 'front' ? inletOff : outletOff;
                        const _lineBlindY = side === 'front' ? slotDepthStart : slotDepthEnd;
                        for (let si = range.startUnit; si < range.endUnit; si++) {
                            const slotBounds = getRackSlotBounds(model, si);
                            const lineY = slotBounds.y + slotBounds.h;
                            var p1, p2;
                            if (_lineBlindOff > 1e-6) {
                                // Lines follow recessed slot face — construct 3D points at _lineBlindZ
                                // 2026-04-18 Y-up: lineY is the vertical boundary (now Y), _lineBlindY was depth (now Z).
                                const _lineBlindZ = side === 'front' ? slotDepthStart : slotDepthEnd;
                                p1 = new THREE.Vector3(slotBounds.x, lineY, _lineBlindZ);
                                p2 = new THREE.Vector3(slotBounds.x + slotBounds.w, lineY, _lineBlindZ);
                                // Offset slightly toward the viewing side to avoid z-fighting
                                p1.z += (side === 'front' ? -0.001 : 0.001);
                                p2.z += (side === 'front' ? -0.001 : 0.001);
                            } else {
                                // No depth offset — use the original layout-based computation
                                p1 = layout.origin.clone()
                                    .add(layout.right.clone().multiplyScalar(slotBounds.x))
                                    .add(layout.up.clone().multiplyScalar(lineY))
                                    .add(faceNormal.clone().multiplyScalar(0.001));
                                p2 = p1.clone().add(layout.right.clone().multiplyScalar(slotBounds.w));
                            }
                            const lineGeom = new THREE.BufferGeometry().setFromPoints([p1, p2]);
                            const _slotLineOp = opacity < 0.05 ? 0 : Math.min(0.15, opacity * opacity * 0.2);
                            if (_slotLineOp > 0.02 && _allModels().length <= 100) {
                                const lineMat = new THREE.LineBasicMaterial({ color: 0x000000, opacity: _slotLineOp, transparent: true });
                                const line = new THREE.Line(lineGeom, lineMat);
                                line.userData = { modelId: model.uid, isSlotBoundaryGuide: true, exportName: null };
                                // Visual only, never a pick target. A THREE.Line is hit anywhere within
                                // Raycaster.params.Line.threshold (1 world unit by default), so a raycast
                                // over the whole scene reported it in front of the real inlet face and the
                                // click selected the neighbouring rack, or no server face at all.
                                line.raycast = function () {};
                                modelGroup.add(line);
                            }
                        }
                    }
                });
        
                // Only create slot guide overlays for the currently selected rack to avoid
                // thousands of unnecessary transparent meshes (200 racks × 42 slots × 2 sides = 16,800).
                // In rack-detail mode the single target rack always gets its guides, so selecting
                // an outlet patch/face/slot needs no geometry rebuild (prevents the click freeze).
                if (ctx.uiState.selection.modelId === model.uid || (isRackDetailMode() && ctx.uiState.rackDetail.target === model.uid)) {
                    for (let slotIndex = 0; slotIndex < geom.slot; slotIndex++) {
                        if (!isRackSlotSelectable(model, slotIndex)) continue;
                        const bounds = getRackSlotBounds(model, slotIndex);
                        // Per-slot depth range (accounts for rack-level + equipment depth offsets)
                        const sdr = getSlotDepthRange(model, slotIndex);
                        const slotY = side === 'front' ? sdr.y0 : sdr.y1;
                        const hasOffset = side === 'front' ? sdr.y0 > 1e-6 : (model.geometry.size.z - sdr.y1) > 1e-6;
                        let guideOrigin, rightVec, upVec;
                        if (hasOffset) {
                            // Slot face is recessed — place highlight at the real slot face depth.
                            // Must use the SAME winding convention as the no-offset branch
                            // (faceLayouts): front right=(-1,0,0), back right=(+1,0,0).
                            const layout = faceLayouts[side];
                            rightVec = layout.right.clone();
                            upVec = layout.up.clone();
                            const pushZ = side === 'front' ? -0.0008 : 0.0008;
                            guideOrigin = layout.origin.clone()
                                .add(layout.right.clone().multiplyScalar(bounds.x))
                                .add(layout.up.clone().multiplyScalar(bounds.y));
                            // Override depth (Z) to the recessed slot face position
                            guideOrigin.z = slotY + pushZ;
                        } else {
                            // No offset — use outer rack face layout
                            const layout = faceLayouts[side];
                            const normal = new THREE.Vector3().crossVectors(layout.right, layout.up).normalize();
                            rightVec = layout.right.clone();
                            upVec = layout.up.clone();
                            guideOrigin = layout.origin.clone()
                                .add(layout.right.clone().multiplyScalar(bounds.x))
                                .add(layout.up.clone().multiplyScalar(bounds.y))
                                .add(normal.multiplyScalar(0.0008));
                        }
                        spawnPlanarRect(modelGroup, guideOrigin, rightVec, upVec, bounds.w, bounds.h, {
                            color: '#4daafc',
                            opacity: 0.015,
                            depthWrite: false,
                            skipEdges: true,
                            userData: {
                                modelId: model.uid,
                                faceId: side,
                                side,
                                slotIndex,
                                isRackSlot: true,
                                isRackSlotGuide: true,
                                exportName: null
                            }
                        });
                    }
                }
            });
        
            getRackServerViews(model).forEach(equipment => {
                const bounds = getRackSlotBounds(model, equipment.startUnit);
                const height = equipment.spanUnits * geom.slotHeight;
        
                // Per-server ABSOLUTE depth: z0 = inletOffset, z1 = D - outletOffset.
                // The rack-level row offsets (slotDepthStart / slotDepthEnd) drive the
                // blank-baffle and U-slot structure only; adding them here is what used
                // to make a row-slider drag reposition every server in the rack.
                var srvDepth = getServerDepthRange(model, equipment);

                // Check if ANY slot in the equipment's span is deleted on each face.
                // If deleted, skip generating that face's solid (the merged wall covers it).
                // NOTE: equipment slots cannot be in "half-dissolved" state because
                // setRackSlotDeleted() prevents deletion of slots occupied by equipment.
                const frontDeleted = Array.from({length: equipment.spanUnits}, (_, i) =>
                    isRackSlotDeleted(model, 'front', equipment.startUnit + i)).some(Boolean);
                const backDeleted = Array.from({length: equipment.spanUnits}, (_, i) =>
                    isRackSlotDeleted(model, 'back', equipment.startUnit + i)).some(Boolean);
        
                // Build the server box in world coordinates
                // 2026-04-18 Y-up: y0/y1 = vertical (slot height along Y); z0/z1 = depth (along Z).
                var serverBox = {
                    x0: bounds.x,
                    x1: bounds.x + bounds.w,
                    y0: bounds.y,
                    y1: bounds.y + height,
                    z0: srvDepth.z0,
                    z1: srvDepth.z1
                };
                // Per-server opacity: a server's opacity is its OWN record's
                // (meta.display.opacity) and nothing else, so the rack slider fades
                // the frame, blanking panels and doors and never the servers. A
                // server that stores none is OPAQUE — what both editors' server
                // sliders show for it (React serverOpacityOf, the legacy server
                // row). Falling back to the rack's value made every server of a
                // document without per-server opacity fade with the frame the
                // moment the rack slider was released (2026-09-23).
                var serverOpacity = (equipment.opacity != null && Number.isFinite(Number(equipment.opacity)))
                    ? Number(equipment.opacity) : 1;
                buildServerGeometry(modelGroup, model, equipment, serverBox, serverOpacity, channelWallColor, {
                    front: frontDeleted, back: backDeleted
                });
            });
        
            (model.constructions.partitions || []).forEach(partition => {
                if (partition.deleted) return;
                // Fully manual: no auto-suppression based on merged ranges or equipment spans.
                // User is responsible for adding/removing partitions via the slot inspector.
                var pRange = getPartitionDepthRange(model, partition.index);
                if (pRange.depth < 1e-6) return;  // skip zero-depth partitions
                // 2026-04-18 Y-up: partition sits at vertical position Y = marginBottom + (index+1)*unitHeight.
                // Depth range spans along Z (was Y).
                const partY = model.safeMarginBottom + (partition.index + 1) * geom.slotHeight;
                spawnPlanarRect(
                    modelGroup,
                    new THREE.Vector3(slotX, partY, pRange.y0),
                    new THREE.Vector3(1, 0, 0),
                    new THREE.Vector3(0, 0, 1),
                    slotWidth,
                    pRange.depth,
                    {
                        color: '#88909b',
                        opacity,
                        doubleSide: true,
                        userData: {
                            modelId: model.uid,
                            isRackPartition: true,
                            partitionIndex: partition.index,
                            exportName: getRackPartitionExportName(model, partition.index)
                        }
                    }
                );
            });
        
            // Through-zone has NO viewport overlay — the through-slots are already
            // visually distinct because both their front and back faces are deleted
            // (you can see straight through the rack at those U-slots). The legacy
            // orange-box mesh was a leakage-porous-zone visualization from before
            // through-zone became pure fluid (2026-04-17 redesign); it now only
            // served to give the zone a click target, which is redundant with
            // clicking the already-visible through-slots.
        
            // P3: Perforated door meshes (hex pattern, click-through)
            // 2026-04-18 Y-up: inlet face is at Z=0, outlet face is at Z=model.geometry.size.z.
            // PlaneGeometry is XY plane by default; rotate around Y-axis to face into Z.
            // V4 doors are geometry.faces entries (uid:'inletDoor'/'outletDoor', role:'door').
            const _rackFaces = Array.isArray(geom.faces) ? geom.faces : [];
            const _inletDoor = _rackFaces.find(f => f && f.uid === 'inletDoor' && f.role === 'door');
            const _outletDoor = _rackFaces.find(f => f && f.uid === 'outletDoor' && f.role === 'door');
            if (_inletDoor && _inletDoor.renderDoor !== false && centralFrameHeight > 1e-6) {
                var inletDoorGeo = new THREE.PlaneGeometry(slotWidth, centralFrameHeight);
                var inletDoorMat = _rackDoorShaderMaterial(0.4, _inletDoor.perforation, slotWidth, centralFrameHeight);
                var inletDoorMesh = new THREE.Mesh(inletDoorGeo, inletDoorMat);
                // Place on inlet face (Z=0), door center in XY plane
                inletDoorMesh.position.set(
                    slotX + slotWidth / 2,
                    safeBottomHeight + centralFrameHeight / 2,
                    -0.001  // tiny offset to avoid z-fighting with face
                );
                // PlaneGeometry is already in XY plane facing ±Z — no rotation needed (DoubleSide material)
                inletDoorMesh.raycast = function() {}; // click-through: visible but not pickable
                inletDoorMesh.renderOrder = 50;
                inletDoorMesh.userData = {
                    modelId: model.uid, isDoor: true, doorSide: 'inlet',
                    exportName: rName + '_inlet_door'
                };
                modelGroup.add(inletDoorMesh);
            }
            if (_outletDoor && _outletDoor.renderDoor !== false && centralFrameHeight > 1e-6) {
                var outletDoorGeo = new THREE.PlaneGeometry(slotWidth, centralFrameHeight);
                var outletDoorMat = _rackDoorShaderMaterial(0.4, _outletDoor.perforation, slotWidth, centralFrameHeight);
                var outletDoorMesh = new THREE.Mesh(outletDoorGeo, outletDoorMat);
                outletDoorMesh.position.set(
                    slotX + slotWidth / 2,
                    safeBottomHeight + centralFrameHeight / 2,
                    model.geometry.size.z + 0.001  // tiny offset past outlet face
                );
                // PlaneGeometry is already in XY plane facing ±Z — no rotation needed (DoubleSide material)
                outletDoorMesh.raycast = function() {}; // click-through: visible but not pickable
                outletDoorMesh.renderOrder = 50;
                outletDoorMesh.userData = {
                    modelId: model.uid, isDoor: true, doorSide: 'outlet',
                    exportName: rName + '_outlet_door'
                };
                modelGroup.add(outletDoorMesh);
            }
            _renderLiquidManifold(model, modelGroup);
        }

        
        function renderWallModel(model, modelGroup, faceLayouts) {
            renderPlanarModelWithPatches(model, modelGroup, faceLayouts, 'front', '#7f848e', 'wall');
        }

        
        function ensureCustomPatch3DDefaults(model, faceId, patch, regionName = null) {
            const center = getRegionCenter(model, regionName || patch.regionName || null);
            if (!Number.isFinite(patch.px)) patch.px = center.x;
            if (!Number.isFinite(patch.py)) patch.py = center.y;
            if (!Number.isFinite(patch.pz)) patch.pz = center.z;
            if (!Number.isFinite(patch.depth)) patch.depth = Math.max(model.geometry.size.x, model.geometry.size.z, model.geometry.size.y) * 0.35;
            const euler = getDefaultPatchEuler(faceId);
            if (!Number.isFinite(patch.rx)) patch.rx = euler.rx;
            if (!Number.isFinite(patch.ry)) patch.ry = euler.ry;
            if (!Number.isFinite(patch.rz)) patch.rz = euler.rz;
        }

        
        function intersectPatchBodyWithRegionPlane(model, faceId, patch, frame) {
            const { cutterGeom } = buildCustomPatchCutterGeometry(model, faceId, patch);
            const pos = cutterGeom.attributes.position;
            const index = cutterGeom.index;
            const count = index ? index.count : pos.count;
            const epsilon = 1e-6;
            const rawPoints = [];
            const pointKeySet = new Set();
            const addPoint = point => {
                const projected = toFramePoint(point, frame);
                const key = `${projected[0].toFixed(6)}:${projected[1].toFixed(6)}`;
                if (pointKeySet.has(key)) return;
                pointKeySet.add(key);
                rawPoints.push(projected);
            };
            const pointAt = idx => new THREE.Vector3().fromBufferAttribute(pos, idx);
        
            for (let i = 0; i < count; i += 3) {
                const a = pointAt(index ? index.getX(i) : i);
                const b = pointAt(index ? index.getX(i + 1) : i + 1);
                const c = pointAt(index ? index.getX(i + 2) : i + 2);
                const triangle = [a, b, c];
                const distances = triangle.map(point => frame.normal.dot(point.clone().sub(frame.origin)));
        
                if (distances.every(distance => Math.abs(distance) <= epsilon)) {
                    triangle.forEach(addPoint);
                    continue;
                }
        
                [[0, 1], [1, 2], [2, 0]].forEach(([startIndex, endIndex]) => {
                    const p1 = triangle[startIndex];
                    const p2 = triangle[endIndex];
                    const d1 = distances[startIndex];
                    const d2 = distances[endIndex];
        
                    if (Math.abs(d1) <= epsilon) addPoint(p1);
                    if (Math.abs(d2) <= epsilon) addPoint(p2);
                    if (d1 * d2 >= -epsilon * epsilon || Math.abs(d1 - d2) <= epsilon) return;
        
                    const t = d1 / (d1 - d2);
                    if (t < -epsilon || t > 1 + epsilon) return;
                    addPoint(p1.clone().lerp(p2, t));
                });
            }
        
            const hull = computeConvexHull2D(rawPoints);
            if (hull.length < 3) return null;
            const closedHull = [...hull, hull[0]];
            return [[[closedHull]]];
        }

        
        function renderPlanarModelWithPatches(model, modelGroup, faceLayouts, primaryFaceId, baseColor, objectKind, patchObjectKind) {
            const _isLiquidDM = (typeof isLiquidMode === 'function') && isLiquidMode();
            const _liquidKs = ['cdu','crh','cold_plate','heat_source','pipe_segment','pipe_junction','rack'];
            const _dimMult = 1.0;
            const layout = faceLayouts[primaryFaceId];
            const W = layout.w, H = layout.h;
            const facesIslands = buildFaceIslands(model);
            const islands = facesIslands[primaryFaceId] || [];
            const face = model.geometry.faces[primaryFaceId];
            const faceOpacity = (face.opacity ?? (model.meta.display.opacity ?? 1)) * _dimMult;
        
            // Polygon floor/ceiling: use polygon vertices as base shape instead of rectangle
            let wallPoly;
            if (model.geometry.polygon && model.geometry.polygon.length >= 3 && (primaryFaceId === 'top' || primaryFaceId === 'bottom')) {
                // polygon-clipping expects MultiPolygon format: [Polygon[Ring[Point]]]
                // Ring must be closed (first point repeated at end)
                const _ring = model.geometry.polygon.map(v => [v[0], v[1]]);
                _ring.push([_ring[0][0], _ring[0][1]]);
                wallPoly = [[_ring]];
            } else {
                // corners dragged off the rectangle (the blue corner handles)
                // make the sheet a general quad
                wallPoly = _pure().quadToPoly(0, 0, W, H, model.geometry.cornerOffsets);
            }
            // Apply cutouts (from deletePatchWithCavity or boolDiff)
            if (Array.isArray(model.geometry.cutouts)) {
                model.geometry.cutouts.forEach(cutout => {
                    if (cutout.faceId === primaryFaceId && cutout.polygon) {
                        try { wallPoly = polygonClipping.difference(wallPoly, cutout.polygon); } catch (e) {}
                    }
                });
            }
            islands.forEach((island, islandIndex) => {
                wallPoly = polygonClipping.difference(wallPoly, island.poly);
                island.poly.forEach(polygon => {
                    const shape = new THREE.Shape();
                    polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                    for (let k = 1; k < polygon.length; k++) {
                        const hole = new THREE.Path();
                        polygon[k].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                        shape.holes.push(hole);
                    }
                    const geom = new THREE.ShapeGeometry(shape, 32);
                    geom.applyMatrix4(layout.mat);
                    var _pOK = patchObjectKind || objectKind;
                    var _pImgSrc = patchImageSource(face, island);
                    if (_pImgSrc) _pOK = FACE_IMAGE_KIND + _pImgSrc;
                    var _pIsImg = (_pOK === 'baffle-grille') || !!_pImgSrc;
                    spawnRenderable(modelGroup, geom, {
                        color: _pIsImg ? '#ffffff' : island.color,
                        opacity: faceOpacity,
                        doubleSide: true,
                        objectKind: _pOK,
                        renderOrder: 3,
                        uvNormalize: _pImgSrc ? { type: 'stretch' } : undefined,
                        userData: { modelId: model.uid, faceId: primaryFaceId, refIds: island.refIds, exportName: composeModelScopedExportName(model, primaryFaceId, face, { isPatch: true, patchIndex: islandIndex, patchCount: islands.length }), isBaseWall: false }
                    });
                });
            });
        
            wallPoly.forEach((polygon, wallIndex) => {
                const wallOpacity = ensureWallOpacity(face, wallIndex) * _dimMult;
                const shape = new THREE.Shape();
                polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                for (let k = 1; k < polygon.length; k++) {
                    const hole = new THREE.Path();
                    polygon[k].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                    shape.holes.push(hole);
                }
                const geom = new THREE.ShapeGeometry(shape, 32);
                geom.applyMatrix4(layout.mat);
                const _sheetImg = faceImageSource(face);
                spawnRenderable(modelGroup, geom, {
                    color: _sheetImg ? '#ffffff' : baseColor,
                    opacity: wallOpacity,
                    doubleSide: true,
                    objectKind: _sheetImg ? (FACE_IMAGE_KIND + _sheetImg) : objectKind,
                    ...(_sheetImg ? { uvNormalize: { type: 'stretch' } } : {}),
                    userData: { modelId: model.uid, faceId: primaryFaceId, refIds: null, wallIndex, exportName: composeModelScopedExportName(model, primaryFaceId, face, { isWallPart: true, partIndex: wallIndex, partCount: wallPoly.length }), isBaseWall: true }
                });
            });
        
        }

            function _buildSingleBoxFace(modelGroup, model, faceLayouts, faceId, facesIslands, dimOpacity) {
            const isACU = model.kind === 'acu';
            const isCDU = model.kind === 'cdu';
            const acuPP = isACU ? cfdprojStateV4.patchPhysicsMap(model) : null;
            const cduFaceColors = { front: '#007a99', back: '#009ab8', left: '#008aaa', right: '#008aaa', top: '#00b4dc', bottom: '#005e77' };
            const _dim = dimOpacity ?? 1.0;
            const face = model.geometry.faces[faceId];
            if (!face) return;
            const layout = faceLayouts[faceId];
            if (!layout) return;
            if (isCDU) layout.color = cduFaceColors[faceId] || '#00b4dc';
            const faceOpacity = (face.opacity ?? 1) * _dim;
            let wallPoly = rectToPoly(0, 0, layout.w, layout.h);
            (facesIslands[faceId] || []).forEach((island, islandIdx) => {
                wallPoly = polygonClipping.difference(wallPoly, island.poly);
                let islandKind = 'box';
                if (isACU && island.refIds?.length) {
                    const role = acuPP[island.refIds[0]]?.role;
                    if (role === 'supply') islandKind = 'acu-supply';
                    else if (role === 'return') islandKind = 'acu-return-photo';
                    else islandKind = 'box';
                }
                var _islandImg = patchImageSource(face, island);
                if (_islandImg) islandKind = FACE_IMAGE_KIND + _islandImg;
                var _isImgIsland = (islandKind === 'acu-return-photo') || !!_islandImg;
                island.poly.forEach(polygon => {
                    const shape = new THREE.Shape();
                    polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                    for (let i = 1; i < polygon.length; i++) {
                        const hole = new THREE.Path();
                        polygon[i].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                        shape.holes.push(hole);
                    }
                    const geom = new THREE.ShapeGeometry(shape, 32);
                    geom.applyMatrix4(layout.mat);
                    var _patchOpts = {
                        color: _isImgIsland ? '#ffffff' : island.color,
                        opacity: faceOpacity,
                        objectKind: islandKind,
                        renderOrder: 3,
                        userData: {
                            modelId: model.uid,
                            faceId,
                            refIds: island.refIds,
                            exportName: composeModelScopedExportName(model, faceId, face, { isPatch: true, patchIndex: islandIdx, patchCount: facesIslands[faceId].length }),
                            isBaseWall: false
                        }
                    };
                    if (_isImgIsland) _patchOpts.uvNormalize = { type: 'stretch' };
                    spawnRenderable(modelGroup, geom, _patchOpts);
                });
            });
        
            wallPoly.forEach((polygon, wallIndex) => {
                const wallOpacity = ensureWallOpacity(face, wallIndex) * _dim;
                const shape = new THREE.Shape();
                polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                for (let i = 1; i < polygon.length; i++) {
                    const hole = new THREE.Path();
                    polygon[i].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                    shape.holes.push(hole);
                }
                const geom = new THREE.ShapeGeometry(shape, 32);
                geom.applyMatrix4(layout.mat);
                const _faceImg = faceImageSource(face);
                // Liquid equipment bodies get their own physically based
                // surfaces (js/pbr_materials.js): a powder-coated CDU cabinet,
                // a copper cold plate. Everything else stays a generic 'box'.
                const _bodyKind = isCDU ? 'cdu-body' : (model.kind === 'cold_plate' ? 'cold-plate' : 'box');
                spawnRenderable(modelGroup, geom, {
                    color: _faceImg ? '#ffffff' : layout.color,
                    opacity: wallOpacity,
                    objectKind: _faceImg ? (FACE_IMAGE_KIND + _faceImg) : _bodyKind,
                    ...(_faceImg ? { uvNormalize: { type: 'stretch' } } : {}),
                    userData: {
                        modelId: model.uid,
                        faceId,
                        refIds: null,
                        wallIndex,
                        exportName: composeModelScopedExportName(model, faceId, face, { isWallPart: true, partIndex: wallIndex, partCount: wallPoly.length }),
                        isBaseWall: true
                    }
                });
            });
        
        }

        
        // Per-face image textures: `face.textureUrl` (a served path such as
        // /textures/electric-box/panel_lamps.jpg) or `face.textureDataUrl` (an
        // image the user loaded from disk, embedded in the .cfdproj). Keyed by
        // the image itself, so two faces showing one picture share a texture.
        const _faceImageTextures = {};
        // A picture on a PATCH (a return grille, a louvred outlet): its own
        // data URL / served URL, else a preset from js/patch_textures.js.
        function patchImageSource(face, island) {
            if (!face || !island || !island.refIds || !island.refIds.length) return null;
            const patch = (face.patches || []).find(p => p && p.id === island.refIds[0]);
            if (!patch) return null;
            if (window.patchTexturePresets && typeof window.patchTexturePresets.sourceFor === 'function') {
                return window.patchTexturePresets.sourceFor(patch);
            }
            const src = patch.textureDataUrl || patch.textureUrl;
            return (typeof src === 'string' && src) ? src : null;
        }
        function faceImageSource(face) {
            if (!face) return null;
            const src = face.textureDataUrl || face.textureUrl;
            return (typeof src === 'string' && src) ? src : null;
        }
        function _faceImageTexture(src) {
            if (_faceImageTextures[src]) return _faceImageTextures[src];
            const tex = new THREE.TextureLoader().load(src, function () {
                clearTimeout(_textureRebuildTimer);
                _textureRebuildTimer = setTimeout(function () {
                    if (typeof ctx.onTextureLoaded === 'function') ctx.onTextureLoaded();
                }, 100);
            });
            tex.colorSpace = THREE.SRGBColorSpace;
            tex.wrapS = THREE.ClampToEdgeWrapping;
            tex.wrapT = THREE.ClampToEdgeWrapping;
            _faceImageTextures[src] = tex;
            return tex;
        }
        const FACE_IMAGE_KIND = 'face-image:';
        // objectKind -> js/pbr_materials.js role. These kinds are
        // MeshStandardMaterial with the library's texture sets; `hueInMap`
        // kinds ignore the model colour because the texture carries the metal.
        const LIBRARY_KINDS = {
            'cdu-body':   { role: 'painted-steel' },
            'cold-plate': { role: 'copper', hueInMap: true },
            'air-duct':   { role: 'galvanized' },
        };
        function _pbrLibrary() {
            return (typeof window !== 'undefined' && window.pbrMaterials) || null;
        }

        /**
         * The repeat a kind's textures need on this mesh (isotropic).
         *
         * 1 where the UVs are already normalised to the picture — `stretch`
         * and `tile-per-u` meshes — and for single photographs wrapped
         * clamp-to-edge; otherwise one tile per `tileSize` metres of the
         * metre-valued UVs (`tile-physical` carries its own tileSize).
         */
        function _textureRepeatFor(typeKey, options) {
            if (typeKey === 'box' || typeKey === 'cdu-body' || typeKey.indexOf(FACE_IMAGE_KIND) === 0) {
                return _boxMaterialRecipes().textureRepeat(options, _boxMaterialContext(typeKey));
            }
            if (typeKey === 'baffle' || typeKey === 'baffle-grille') {
                return _baffleMaterials().materialPolicy(typeKey, options, _boxMaterialContext(typeKey)).texture.repeat;
            }
            const n = options && options.uvNormalize;
            if (n && (n.type === 'stretch' || n.type === 'tile-per-u')) return 1;
            const ext = externalTextureRegistry[typeKey];
            const base = ext && (ext.diffuse || ext.normal);
            if (base && base.wrapS === THREE.ClampToEdgeWrapping) return 1;
            const tile = (n && n.tileSize) || TEXTURE_TILE_SIZES[typeKey] || 0.4;
            return Math.round(1000 / tile) / 1000;
        }

        function _textureAtRepeat(tex, repeat) {
            if (!tex || repeat === 1) return tex;
            const key = tex.uuid + '|' + repeat;
            let clone = _repeatTextureCache.get(key);
            if (!clone) {
                clone = tex.clone();
                clone.repeat.set(repeat, repeat);
                if (tex._isCached) clone._isCached = true;
                _repeatTextureCache.set(key, clone);
            }
            return clone;
        }

        function createMaterialForType(typeKey, options) {
            if (typeKey.indexOf(FACE_IMAGE_KIND) === 0) {
                // A photograph on the face: the picture IS the colour, so no tint,
                // no procedural map; the UVs are stretched over the face by
                // spawnRenderable's uvNormalize.
                const src = typeKey.slice(FACE_IMAGE_KIND.length);
                const policy = _boxMaterialRecipes().materialPolicy(typeKey, options, _boxMaterialContext(typeKey));
                const props = Object.assign({}, policy.parameters, {
                    map: _faceImageTexture(src), side: policy.parameters.side === 'double' ? THREE.DoubleSide : THREE.FrontSide
                });
                return _withPbrHint(new THREE.MeshPhongMaterial(props), policy.pbr.roughness, policy.pbr.metalness);
            }

            const libKind = LIBRARY_KINDS[typeKey];
            if (libKind && _pbrLibrary()) {
                if (typeKey === 'cdu-body') {
                    const policy = _boxMaterialRecipes().materialPolicy(typeKey, options, _boxMaterialContext(typeKey));
                    const params = Object.assign({}, policy.parameters, {
                        side: policy.parameters.side === 'double' ? THREE.DoubleSide : THREE.FrontSide
                    });
                    return _pbrLibrary().material(policy.pbr.role, params);
                }
                const libOpacity = options.opacity ?? 1;
                const libTransparent = typeof options.transparent === 'boolean'
                    ? options.transparent : libOpacity < 0.999;
                const params = {
                    side: options.doubleSide ? THREE.DoubleSide : THREE.FrontSide,
                    polygonOffset: true,
                    polygonOffsetFactor: options.polygonOffsetFactor ?? 1,
                    transparent: libTransparent,
                    opacity: libOpacity,
                    depthWrite: options.depthWrite ?? !libTransparent,
                };
                if (!libKind.hueInMap || options.keepUserColor) params.color = options.color;
                return _pbrLibrary().material(libKind.role, params);
            }
            const preset = _materialPresets[typeKey] || _materialPresets['box'];
            const scopedPolicy = typeKey === 'box' || typeKey === 'cdu-body'
                ? _boxMaterialRecipes().materialPolicy(typeKey, options, _boxMaterialContext(typeKey))
                : typeKey === 'baffle' || typeKey === 'baffle-grille'
                    ? _baffleMaterials().materialPolicy(typeKey, options, _boxMaterialContext(typeKey)) : null;
            const baseOpacity = scopedPolicy ? scopedPolicy.parameters.opacity : options.opacity ?? 1;
            const isTransparent = scopedPolicy ? scopedPolicy.parameters.transparent
                : typeof options.transparent === 'boolean' ? options.transparent : baseOpacity < 0.999;
        
            const ext = externalTextureRegistry[typeKey];
            const proc = (typeKey !== 'custom') ? _getOrCreateTexture(typeKey) : null;
        
            // Thin surfaces (blind panels, baffles) must render both sides
            // so they remain visible when the opposite panel is removed
            const _thinSurface = (typeKey === 'blank-panel' || typeKey === 'baffle');
            const matProps = scopedPolicy ? Object.assign({}, scopedPolicy.parameters, {
                side: scopedPolicy.parameters.side === 'double' ? THREE.DoubleSide : THREE.FrontSide
            }) : {
                ...(options.color !== undefined ? { color: options.color } : {}),
                shininess: Math.round((1 - preset.roughness) * 80) + 10,
                specular: 0x111111,
                side: (options.doubleSide || _thinSurface) ? THREE.DoubleSide : THREE.FrontSide,
                polygonOffset: true,
                polygonOffsetFactor: options.polygonOffsetFactor ?? 1,
                transparent: isTransparent,
                opacity: baseOpacity,
                depthWrite: options.depthWrite ?? !isTransparent,
            };
        
            const repeat = _textureRepeatFor(typeKey, options);
            if (proc || ext) {
                const diffTex = _textureAtRepeat(ext?.diffuse || proc?.diffuse || null, repeat);
                if (diffTex) {
                    matProps.map = diffTex;
                    // Apply tint color: preset.tintColor overrides, null = keep model color.
                    // When keepUserColor is set (e.g. patches), always use the user-defined color.
                    if (!options.keepUserColor && (ext?.diffuse || proc?.diffuse)) {
                        if (preset.tintColor !== undefined && preset.tintColor !== null) {
                            matProps.color = preset.tintColor;
                        }
                        // tintColor === null → keep options.color (model-defined)
                    }
                }
                const normalScale = scopedPolicy ? scopedPolicy.pbr.normalScale : preset.normalScale;
                if (normalScale > 0) {
                    const normTex = _textureAtRepeat(ext?.normal || proc?.normal || null, repeat);
                    if (normTex) {
                        matProps.normalMap = normTex;
                        matProps.normalScale = new THREE.Vector2(normalScale, normalScale);
                    }
                }
            }
        
            return _withPbrHint(new THREE.MeshPhongMaterial(matProps), scopedPolicy ? scopedPolicy.pbr.roughness : preset.roughness, scopedPolicy ? scopedPolicy.pbr.metalness : preset.metalness);
        }

        /**
         * The preset's physically based values, for renderers that are PBR.
         *
         * Three draws these kinds with Phong (roughness becomes shininess and
         * metalness is dropped); the Omniverse snapshot reads
         * `userData.pbr` instead of re-deriving roughness from shininess, so a
         * rack reads as dark gunmetal there rather than as glossy plastic.
         * Plain numbers only — Material.copy deep-copies userData via JSON.
         */
        function _withPbrHint(material, roughness, metalness) {
            material.userData.pbr = { roughness: roughness, metalness: metalness };
            return material;
        }

        
        function getAppliedCustomPatchFragments(model, faceId, patch) {
            const cacheEntry = ctx.geometryCache.get(model.uid);
            if (!patch.applied || !canUseSurfaceProjectionFallback(cacheEntry)) return [];
            return cacheEntry.regions.flatMap(region => {
                if (patch.regionName && patch.regionName !== region.name) return [];
                const frame = getPlanarRegionFrame(region, model);
                const basePolys = regionGeometryToPolygons(region, frame, model);
                const intersected = intersectPatchBodyWithRegionPlane(model, faceId, patch, frame);
                if (!intersected) return [];
                let clipped = [];
                try { clipped = polygonClipping.intersection(basePolys, intersected); } catch (e) { clipped = []; }
                if (!clipped || !clipped.length) return [];
                return [{ regionName: region.name, polygons: clipped, frame }];
            });
        }

        
        function isLiquidMode() {
            return ctx.uiState.displayMode === 'liquid';
        }

        
        function spawnRenderable(modelGroup, geom, options) {
            const baseOpacity = options.opacity ?? 1;
            const isTransparent = typeof options.transparent === 'boolean' ? options.transparent : baseOpacity < 0.999;
            // Patches (renderOrder > 0) need polygonOffsetFactor=0 so they render
            // in front of co-planar base walls (factor=1), and keepUserColor so the
            // user-defined patch color isn't overridden by the material preset tint.
            const isPatchOverlay = Number.isFinite(options.renderOrder) && options.renderOrder > 0;
            if (isPatchOverlay) {
                if (options.objectKind === 'box' || options.objectKind === 'cdu-body' || (options.objectKind && options.objectKind.indexOf(FACE_IMAGE_KIND) === 0)) {
                    const defaults = _boxMaterialRecipes().overlayDefaults(options);
                    if (options.polygonOffsetFactor === undefined) options.polygonOffsetFactor = defaults.polygonOffsetFactor;
                    if (options.keepUserColor === undefined) options.keepUserColor = defaults.keepUserColor;
                } else {
                    if (options.polygonOffsetFactor === undefined) options.polygonOffsetFactor = 0;
                    if (options.keepUserColor === undefined) options.keepUserColor = true;
                }
            }
            let material;
            if (ctx.webglAvailable) {
                material = options.objectKind
                    ? getCachedMaterial(options.objectKind, options)
                    : new THREE.MeshPhongMaterial({
                        ...(options.color !== undefined ? { color: options.color } : {}),
                        shininess: 30,
                        specular: 0x111111,
                        side: options.doubleSide ? THREE.DoubleSide : THREE.FrontSide,
                        polygonOffset: true,
                        polygonOffsetFactor: options.polygonOffsetFactor ?? 1,
                        transparent: isTransparent,
                        opacity: baseOpacity,
                        depthWrite: options.depthWrite ?? !isTransparent
                    });
            } else {
                material = new THREE.MeshBasicMaterial({ color: options.color || 0xcccccc });
            }
            const mesh = new THREE.Mesh(geom, material);
            mesh.castShadow = false;
            mesh.receiveShadow = false;
            if (Number.isFinite(options.renderOrder)) mesh.renderOrder = options.renderOrder;
            if (options.uvNormalize && geom.attributes && geom.attributes.uv) {
                var uvAttr = geom.attributes.uv;
                var uvMode = options.uvNormalize;
                if (uvMode.type === 'stretch' || uvMode.type === 'tile-per-u') {
                    var minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
                    for (var i = 0; i < uvAttr.count; i++) {
                        var u = uvAttr.getX(i), v = uvAttr.getY(i);
                        if (u < minU) minU = u; if (u > maxU) maxU = u;
                        if (v < minV) minV = v; if (v > maxV) maxV = v;
                    }
                    var rangeU = (maxU - minU) || 1, rangeV = (maxV - minV) || 1;
                    var scaleV = (uvMode.type === 'tile-per-u') ? (uvMode.uCount || 1) : 1;
                    for (var i = 0; i < uvAttr.count; i++) {
                        uvAttr.setX(i, (uvAttr.getX(i) - minU) / rangeU);
                        uvAttr.setY(i, ((uvAttr.getY(i) - minV) / rangeV) * scaleV);
                    }
                    uvAttr.needsUpdate = true;
                }
            }
            // Library (MeshStandardMaterial) kinds: the textures are shared by
            // every mesh of every kind that uses the set, so tiling goes into
            // this mesh's own UVs — metres in, tiles out — and the textures'
            // repeat is never written.
            const _pbrRole = material.userData && material.userData.pbrRole;
            if (_pbrRole && geom.attributes && geom.attributes.uv && !options.uvNormalize && !options.pbrUvReady && _pbrLibrary()) {
                const _tile = (_pbrLibrary().ROLES[_pbrRole] || {}).tileM || 0.4;
                const scale = options.objectKind === 'cdu-body'
                    ? _boxMaterialRecipes().materialPolicy(options.objectKind, options, _boxMaterialContext(options.objectKind)).uv.physicalScale
                    : 1 / _tile;
                _pbrLibrary().scaleUv(geom, scale, scale);
            }
            // Texture tiling for the preset kinds is decided when the material
            // is made (createMaterialForType -> _textureRepeatFor), never by
            // writing a repeat onto a texture other meshes share.
            // Skip edge wireframes when scene has many models (>100) to reduce draw calls by ~50%.
            // EdgesGeometry is also expensive to compute (convex hull per mesh).
            const _skipEdgesForPerf = _allModels().length > 100;
            const _edgeOpacity = baseOpacity < 0.05 ? 0 : Math.min(0.3, baseOpacity * baseOpacity * 0.4);
            if (ctx.webglAvailable && !options.skipEdges && !_skipEdgesForPerf && _edgeOpacity > 0.02) {
                mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geom), new THREE.LineBasicMaterial({
                    color: 0x000000,
                    opacity: _edgeOpacity,
                    transparent: true,
                    depthWrite: false
                })));
            }
            mesh.userData = {
                ...options.userData,
                origColor: options.color,
                baseOpacity,
                _materialPreset: options.objectKind || null
            };
            modelGroup.add(mesh);
            ctx.renderMeshes.push(mesh);
            return mesh;
        }

            function buildRackFrameGeometry(modelGroup, model, faceLayouts, fp) {
            var framePrefix = sanitizeName(fp.rName + '_frame');
            var frameColor = '#5c6370';
            var sideColor  = '#7f848e';
            var topColor   = '#9ba0a9';
            var bottomColor = '#4b5263';
            var innerColor = '#707783';
        
            var solidNames = [];
            var unifiedName = framePrefix;  // `{rack}_frame`
            var makeUD = function(subKind) {
                // When MERGE_RACK_FRAME_SOLIDS is on, all 20 sub-meshes share the
                // single `{rack}_frame` exportName → exportBoxModel groups them
                // into one `solid` block in merged_rack_frame.stl.
                var name = window.MERGE_RACK_FRAME_SOLIDS
                    ? unifiedName
                    : sanitizeName(framePrefix + '_' + subKind);
                if (solidNames.indexOf(name) === -1) solidNames.push(name);
                return { modelId: model.uid, isBaseWall: true, isRackFrame: true, exportName: name };
            };
        
            var rackFaceKind = { left: 'rack-side', right: 'rack-side', top: 'rack-top', bottom: 'rack-bottom', front: 'rack-front', back: 'rack-front' };
        
            // Local spawnFaceRect — mirrors the closure in renderRackModel but uses
            // per-subregion unique exportNames and model-level opacity.
            // doubleSide is ALWAYS true for rack-frame faces: a rack is a hollow
            // solid with an interior cavity, so every face must render from both
            // the outside AND inside. FrontSide culling made inner walls and
            // outer shell backs invisible when the camera was inside the cavity.
            var spawnFaceRect = function(faceId, x, y, width, height, color, extraUD, objectKindOverride, uvNormalize) {
                var layout = faceLayouts[faceId];
                var faceOpacity = (model.geometry.faces[faceId]?.opacity ?? (model.meta.display.opacity ?? 1)) * fp.dimMult;
                var opts = {
                    color: color,
                    opacity: faceOpacity,
                    objectKind: objectKindOverride || rackFaceKind[faceId] || 'rack-side',
                    doubleSide: true,
                    userData: Object.assign({ modelId: model.uid, faceId: faceId }, extraUD)
                };
                if (uvNormalize) opts.uvNormalize = uvNormalize;
                return spawnPlanarRect(modelGroup,
                    layout.origin.clone().add(layout.right.clone().multiplyScalar(x)).add(layout.up.clone().multiplyScalar(y)),
                    layout.right.clone(), layout.up.clone(), width, height, opts);
            };
        
            // --- Outer shell: Left, Right, Top, Bottom ---
            spawnFaceRect('left',   0, 0, fp.D, fp.H, sideColor,   makeUD('outer_left'));
            spawnFaceRect('right',  0, 0, fp.D, fp.H, sideColor,   makeUD('outer_right'));
            spawnFaceRect('top',    0, 0, fp.W, fp.D, topColor,    makeUD('outer_top'));
            spawnFaceRect('bottom', 0, 0, fp.W, fp.D, bottomColor, makeUD('outer_bottom'));
        
            // --- Front/Back: margin strips + frame strips ---
            ['front', 'back'].forEach(function(side) {
                var color = side === 'front' ? frameColor : innerColor;
                // Bottom margin strip
                if (fp.marginBottom > 1e-6) {
                    spawnFaceRect(side, 0, 0, fp.W, fp.marginBottom, color,
                        Object.assign({ side: side }, makeUD(side + '_margin_bottom')));
                }
                // Top margin strip
                if (fp.marginTop > 1e-6) {
                    spawnFaceRect(side, 0, fp.H - fp.marginTop, fp.W, fp.marginTop, color,
                        Object.assign({ side: side }, makeUD(side + '_margin_top')));
                }
                // Left frame strip
                if (fp.slotX > 1e-6 && fp.slotAreaHeight > 1e-6) {
                    var lStripOK = (side === 'front') ? 'rack-strip-left' : undefined;
                    var lStripUV = (side === 'front') ? { type: 'stretch' } : undefined;
                    spawnFaceRect(side, 0, fp.marginBottom, fp.slotX, fp.slotAreaHeight, color,
                        Object.assign({ side: side }, makeUD(side + '_strip_left')), lStripOK, lStripUV);
                }
                // Right frame strip
                var rightFrameW = fp.W - fp.slotX - fp.slotWidth;
                if (rightFrameW > 1e-6 && fp.slotAreaHeight > 1e-6) {
                    var rStripOK = (side === 'front') ? 'rack-strip-right' : undefined;
                    var rStripUV = (side === 'front') ? { type: 'stretch' } : undefined;
                    spawnFaceRect(side, fp.slotX + fp.slotWidth, fp.marginBottom, rightFrameW, fp.slotAreaHeight, color,
                        Object.assign({ side: side }, makeUD(side + '_strip_right')), rStripOK, rStripUV);
                }
            });
        
            // --- Inner cavity walls ---
            // 2026-04-18 Y-up: depth is along Z, height is along Y.
            var effectiveSlotDepth = fp.slotDepthEnd - fp.slotDepthStart;
            if (fp.slotAreaHeight > 1e-6 && effectiveSlotDepth > 1e-6) {
                // Inner left: X=slotX plane, spanning Z(depth) × Y(height)
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(fp.slotX, fp.marginBottom, fp.slotDepthStart),
                    new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0),
                    effectiveSlotDepth, fp.slotAreaHeight,
                    { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
                      doubleSide: true,
                      userData: makeUD('inner_left') });
                // Inner right: X=slotX+slotWidth plane, spanning Y(height) × Z(depth)
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(fp.slotX + fp.slotWidth, fp.marginBottom, fp.slotDepthStart),
                    new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
                    fp.slotAreaHeight, effectiveSlotDepth,
                    { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
                      doubleSide: true,
                      userData: makeUD('inner_right') });
            }
        
            // --- Inner ceiling + floor (span full rack depth to close inlet/outlet cavities) ---
            if (fp.slotWidth > 1e-6) {
                var fullFloorDepth = fp.D;  // full rack depth
                // Inner floor (only needed if marginBottom > 0, otherwise outer bottom covers it)
                // 2026-04-18 Y-up: floor is at Y=marginBottom, spans X × Z(depth)
                if (fp.marginBottom > 1e-6) {
                    spawnPlanarRect(modelGroup,
                        new THREE.Vector3(fp.slotX, fp.marginBottom, 0),
                        new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1),
                        fp.slotWidth, fullFloorDepth,
                        { color: bottomColor, opacity: fp.opacity, doubleSide: true,
                          userData: makeUD('inner_floor') });
                }
                // Inner ceiling (only needed if marginTop > 0, otherwise outer top covers it)
                // 2026-04-18 Y-up: ceiling is at Y=marginBottom+slotAreaHeight, spans X × Z(depth reversed)
                if (fp.marginTop > 1e-6) {
                    spawnPlanarRect(modelGroup,
                        new THREE.Vector3(fp.slotX, fp.marginBottom + fp.slotAreaHeight, fp.D),
                        new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, -1),
                        fp.slotWidth, fullFloorDepth,
                        { color: topColor, opacity: fp.opacity, doubleSide: true,
                          userData: makeUD('inner_ceiling') });
                }
            }
        
            // --- Depth offset cavity walls ---
            // 2026-04-18 Y-up: inlet is at Z=0, outlet is at Z=slotDepthEnd; height along Y.
            if (fp.inletOff > 1e-6 && fp.slotAreaHeight > 1e-6) {
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(fp.slotX, fp.marginBottom, 0),
                    new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0),
                    fp.inletOff, fp.slotAreaHeight,
                    { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
                      doubleSide: true,
                      userData: makeUD('inlet_cavity_left') });
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(fp.slotX + fp.slotWidth, fp.marginBottom, 0),
                    new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
                    fp.slotAreaHeight, fp.inletOff,
                    { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
                      doubleSide: true,
                      userData: makeUD('inlet_cavity_right') });
            }
            if (fp.outletOff > 1e-6 && fp.slotAreaHeight > 1e-6) {
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(fp.slotX, fp.marginBottom, fp.slotDepthEnd),
                    new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0),
                    fp.outletOff, fp.slotAreaHeight,
                    { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
                      doubleSide: true,
                      userData: makeUD('outlet_cavity_left') });
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(fp.slotX + fp.slotWidth, fp.marginBottom, fp.slotDepthEnd),
                    new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
                    fp.slotAreaHeight, fp.outletOff,
                    { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
                      doubleSide: true,
                      userData: makeUD('outlet_cavity_right') });
            }
        
            return solidNames;
        }

        
        function buildServerGeometry(modelGroup, model, equipment, serverBox, opacity, channelWallColor, deletedFaces) {
            var faceIds = ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'];
            for (var fi = 0; fi < faceIds.length; fi++) {
                _buildSingleServerFace(modelGroup, model, equipment, serverBox, opacity, channelWallColor, faceIds[fi], deletedFaces);
            }
        }

        
        function isRackDetailMode() {
            return ctx.uiState.rackDetail.target !== null;
        }

        
        function spawnPlanarRect(modelGroup, origin, right, up, width, height, options) {
            if (width <= 1e-6 || height <= 1e-6) return null;
            const shape = new THREE.Shape();
            shape.moveTo(0, 0);
            shape.lineTo(width, 0);
            shape.lineTo(width, height);
            shape.lineTo(0, height);
            shape.lineTo(0, 0);
            const geom = new THREE.ShapeGeometry(shape, 1);
            const normal = new THREE.Vector3().crossVectors(right, up).normalize();
            const mat = new THREE.Matrix4().makeBasis(right.clone(), up.clone(), normal).setPosition(origin.clone());
            geom.applyMatrix4(mat);
            // 2026-04-17: optional winding flip. Some callers pass right/up vectors
            // whose cross product points INTO the solid (e.g. fan-server
            // top/bottom/left/right faces, where right/up were chosen to keep
            // patch UV orientation stable across drags). Setting flipWinding
            // reverses triangle index order so STL export (which recomputes
            // normals from winding) produces outward-facing facets. Render
            // appearance is unaffected for doubleSide materials.
            if (options && options.flipWinding) {
                const idx = geom.index;
                if (idx) {
                    for (let i = 0; i + 2 < idx.count; i += 3) {
                        const a = idx.getX(i);
                        const c = idx.getX(i + 2);
                        idx.setX(i, c);
                        idx.setX(i + 2, a);
                    }
                    idx.needsUpdate = true;
                }
            }
            return spawnRenderable(modelGroup, geom, options);
        }

        
        function getRegionCenter(model, regionName) {
            const region = getRegionGeometryEntry(model, regionName);
            if (region && region.geometry.boundingBox) {
                const center = region.geometry.boundingBox.getCenter(new THREE.Vector3());
                const _co = model.customMesh ? model.customMesh.offset : { x: 0, y: 0, z: 0 };
                center.add(new THREE.Vector3(_co.x, _co.y, _co.z));
                return center;
            }
            // 2026-04-18 Y-up fallback: H on Y, D on Z.
            return new THREE.Vector3(model.geometry.size.x / 2, model.geometry.size.y / 2, model.geometry.size.z / 2);
        }

        
        function _getOrCreateTexture(typeKey) {
            if (!_textureCache[typeKey]) {
                _textureCache[typeKey] = _generateProceduralTexture(typeKey);
            }
            return _textureCache[typeKey];
        }

            function getCachedMaterial(typeKey, options) {
            const colorHex = typeof options.color === 'number' ? options.color
                : new THREE.Color(options.color).getHex();
            const opKey = Math.round((options.opacity ?? 1) * 100);
            const dsKey = options.doubleSide ? 1 : 0;
            const pofKey = options.polygonOffsetFactor ?? 1;
            const kucKey = options.keepUserColor ? 1 : 0;
            const repKey = _textureRepeatFor(typeKey, options);
            const cacheKey = `${typeKey}|${colorHex}|${opKey}|${dsKey}|${pofKey}|${kucKey}|${repKey}`;
            if (!_materialCache[cacheKey]) {
                _materialCache[cacheKey] = createMaterialForType(typeKey, options);
            }
            return _materialCache[cacheKey];
        }

        
        function _buildSingleServerFace(modelGroup, model, equipment, serverBox, opacity, channelWallColor, faceId, deletedFaces) {
            var slotDepth = serverBox.y1 - serverBox.y0;
            if (faceId === 'inlet' && deletedFaces && deletedFaces.front) return;
            if (faceId === 'outlet' && deletedFaces && deletedFaces.back) return;
            // 2026-09-18 (WP-W6): left/right are BUILT again — a server renders
            // and exports as a closed box. They used to be skipped because they
            // coincide with the rack frame's inner cavity walls, which left the
            // server invisible from the side the moment the user lowered its
            // opacity. The coincidence is now handled DOWNSTREAM, not by hiding
            // the faces: the STL export routes them to their own bucket
            // (merged_server_interface_walls.stl — see stl_naming.js
            // _classifyRackSolidToBucketFile), and the cfdrun manifest /
            // boundaryBindings keep enumerating ONLY the four fluid faces
            // (cfdrun_build.js _buildServerFacesExport), so the mesh pipeline
            // still sees exactly the closed fluid partition it sees today.

            var faceDef = (equipment.faces && equipment.faces[faceId]) || { patches: [] };
            var fr = getServerFaceRect(serverBox, faceId);
            if (!fr || fr.w < 1e-6 || fr.h < 1e-6) return;
        
            // Face color:
            // - inlet/outlet: per-equipment face color
            // - top/bottom: channelWallColor for fanServer, equipment inlet color for nonFan
            // - left/right: channelWallColor for both
            var faceColor;
            if (faceId === 'inlet')       faceColor = getEquipmentFaceColor(equipment, 'inlet');
            else if (faceId === 'outlet') faceColor = getEquipmentFaceColor(equipment, 'outlet');
            else if ((faceId === 'top' || faceId === 'bottom') && equipment.kind !== 'fanServer')
                                          faceColor = getEquipmentFaceColor(equipment, 'inlet');
            else                          faceColor = channelWallColor;
        
            var isInletOutlet = (faceId === 'inlet' || faceId === 'outlet');
            // Inlet (air intake) uses the hexagonal perforated mesh ('网状') texture —
            // physically the server front grille — instead of the flat labelled photo
            // ('server-front-photo'), matching the outlet's perforated-mesh look.
            var objectKind = (faceId === 'inlet') ? 'server-front'
                           : (faceId === 'outlet') ? 'server-rear-socket'
                           : undefined;
            // 2026-09-18 (WP-W6): ALL six faces render double-sided. Inlet and
            // outlet used to be single-sided, so a transparent server backface-
            // culled the far inlet/outlet and the user staring through the box
            // saw neither its far end nor (before this fix) its sides. Material
            // `side` is a render property only — the exported STL winding and
            // facet normals come from flipWinding below and are untouched.
            var doubleSide = true;

            var baseUserData = {
                modelId: model.uid,
                isRackEquipment: true,
                equipmentId: equipment.id,
                faceId: faceId,
                isPatch: false,
                patchId: null
            };
        
            // 2026-04-17: top/bottom/left/right of getServerFaceRect return
            // right/up vectors whose cross product points INTO the equipment
            // (by design — to keep patch UV orientation consistent). For STL
            // export we need outward-facing triangles, so flipWinding on these
            // four faces. inlet/outlet already produce outward normals.
            var flipWinding = (faceId === 'top' || faceId === 'bottom'
                               || faceId === 'left' || faceId === 'right');
        
            if (faceDef.patches && faceDef.patches.length > 0) {
                _buildServerFaceWithPatches(modelGroup, model, equipment, faceId, fr, faceDef, faceColor,
                    opacity, objectKind, doubleSide, baseUserData, [], flipWinding);
            } else {
                var exportName = getServerFaceExportName(model, equipment, faceId);
                var spawnOpts = {
                    color: faceColor, opacity: opacity,
                    doubleSide: doubleSide,
                    flipWinding: flipWinding,
                    userData: Object.assign({}, baseUserData, {
                        exportName: exportName,
                        side: (faceId === 'inlet') ? 'front' : (faceId === 'outlet') ? 'back' :
                              (faceId === 'top' || faceId === 'bottom') ? (equipment.kind === 'fanServer' ? 'channel' : faceId) : faceId
                    })
                };
                if (objectKind) spawnOpts.objectKind = objectKind;
                if (isInletOutlet) spawnOpts.uvNormalize = { type: 'tile-per-u', uCount: equipment.spanUnits };
                spawnPlanarRect(modelGroup, fr.origin, fr.right, fr.up, fr.w, fr.h, spawnOpts);
            }
        }

        
        function getRegionGeometryEntry(model, regionName) {
            const cacheEntry = ctx.geometryCache.get(model.uid);
            if (!cacheEntry || !cacheEntry.regions.length || !regionName) return null;
            return cacheEntry.regions.find(region => region.name === regionName) || null;
        }

        
        function _buildServerFaceWithPatches(modelGroup, model, equipment, faceId, fr, faceDef, faceColor,
            opacity, objectKind, doubleSide, baseUserData, renderMeshes, flipWinding) {
        
            var isOutlet = (faceId === 'outlet');
            var patchOK = isOutlet ? 'server-outlet-mesh' : objectKind;
            var patchUV = isOutlet
                ? { type: 'tile-physical', tileSize: 0.1 }
                : { type: 'tile-per-u', uCount: equipment.spanUnits };
            var wallOK = objectKind;
            var wallUV = (faceId === 'inlet' || isOutlet)
                ? { type: 'tile-per-u', uCount: equipment.spanUnits }
                : undefined;
        
            // Build islands from patches (same logic as buildFaceIslands)
            var islands = [];
            faceDef.patches.forEach(function(patch, pIdx) {
                var poly = patch.type === 'circle'
                    ? circleToPoly(patch.cx, patch.cy, patch.r)
                    : rectToPoly(patch.x, patch.y, patch.w, patch.h);
        
                var current = {
                    poly: poly,
                    name: getServerFaceExportName(model, equipment, faceId, { isPatch: true, patchIndex: pIdx }),
                    color: patch.color || '#00cc66',
                    refIds: [patch.id]
                };
        
                var nextIslands = [];
                islands.forEach(function(island) {
                    var inter = [];
                    try { inter = polygonClipping.intersection(island.poly, current.poly); } catch (e) {}
                    if (inter && inter.length > 0) {
                        current.poly = polygonClipping.union(current.poly, island.poly);
                        current.refIds = current.refIds.concat(island.refIds);
                    } else {
                        nextIslands.push(island);
                    }
                });
                nextIslands.push(current);
                islands = nextIslands;
            });
        
            // Build 3D transform matrix for this face
            var normal = new THREE.Vector3().crossVectors(fr.right, fr.up).normalize();
            var faceMat = new THREE.Matrix4().makeBasis(
                fr.right.clone(), fr.up.clone(), normal
            ).setPosition(fr.origin.clone());
        
            // Render each island (patch region)
            islands.forEach(function(island, islandIdx) {
                island.poly.forEach(function(polygon) {
                    var shape = new THREE.Shape();
                    polygon[0].forEach(function(pt, i) {
                        if (i === 0) shape.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                        else shape.lineTo(pt[0] * fr.w, pt[1] * fr.h);
                    });
                    for (var hi = 1; hi < polygon.length; hi++) {
                        var hole = new THREE.Path();
                        polygon[hi].forEach(function(pt, j) {
                            if (j === 0) hole.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                            else hole.lineTo(pt[0] * fr.w, pt[1] * fr.h);
                        });
                        shape.holes.push(hole);
                    }
        
                    var geom = new THREE.ShapeGeometry(shape, 32);
                    geom.applyMatrix4(faceMat);
                    if (flipWinding) _flipGeometryWinding(geom);
        
                    var _imgPatch = (patchOK === 'server-outlet-mesh' || patchOK === 'server-front-photo' || patchOK === 'server-front');
                    var mesh = spawnRenderable(modelGroup, geom, {
                        color: _imgPatch ? '#ffffff' : SERVER_PATCH_TINT,
                        opacity: opacity,
                        objectKind: patchOK,
                        doubleSide: doubleSide,
                        renderOrder: 2,
                        uvNormalize: patchUV,
                        userData: Object.assign({}, baseUserData, {
                            isPatch: true,
                            patchId: island.refIds[0],
                            // refIds[] lets the generic boxPatchHit drag branch find this mesh
                            // just like box/baffle/floor patches, so we inherit drag/resize/snap.
                            refIds: island.refIds.slice(),
                            exportName: island.name,
                            side: faceId
                        })
                    });
                    if (mesh) renderMeshes.push(mesh);
                });
            });
        
            // Render wall remainder (face minus all islands)
            var wallPoly = [[ [0, 0], [1, 0], [1, 1], [0, 1], [0, 0] ]];
            islands.forEach(function(island) {
                try { wallPoly = polygonClipping.difference(wallPoly, island.poly); } catch (e) {}
            });
        
            var wallExportName = getServerFaceExportName(model, equipment, faceId, { isWall: true });
            wallPoly.forEach(function(polygon) {
                if (!polygon || !polygon[0] || polygon[0].length < 4) return;
                var shape = new THREE.Shape();
                polygon[0].forEach(function(pt, i) {
                    if (i === 0) shape.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                    else shape.lineTo(pt[0] * fr.w, pt[1] * fr.h);
                });
                for (var hi = 1; hi < polygon.length; hi++) {
                    var hole = new THREE.Path();
                    polygon[hi].forEach(function(pt, j) {
                        if (j === 0) hole.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                        else hole.lineTo(pt[0] * fr.w, pt[1] * fr.h);
                    });
                    shape.holes.push(hole);
                }
        
                var geom = new THREE.ShapeGeometry(shape, 32);
                geom.applyMatrix4(faceMat);
                if (flipWinding) _flipGeometryWinding(geom);
        
                var mesh = spawnRenderable(modelGroup, geom, {
                    color: faceColor,
                    opacity: opacity,
                    objectKind: wallOK,
                    doubleSide: doubleSide,
                    uvNormalize: wallUV,
                    userData: Object.assign({}, baseUserData, {
                        isPatch: false,
                        isWallRemainder: true,
                        exportName: wallExportName,
                        side: faceId
                    })
                });
                if (mesh) renderMeshes.push(mesh);
            });
        
        }

            function clearMaterialCache() {
                Object.keys(_materialCache).forEach(k => delete _materialCache[k]);
                _repeatTextureCache.clear();
            }

            function loadExternalTexture(typeKey, mapType, url, rotation, wrapMode) {
            const loader = new THREE.TextureLoader();
            loader.load(url, (texture) => {
                texture.wrapS = texture.wrapT = wrapMode ?? THREE.RepeatWrapping;
                const isDataMap = (mapType === 'normal' || mapType === 'roughness' || mapType === 'metalness');
                texture.colorSpace = isDataMap ? THREE.LinearSRGBColorSpace : THREE.SRGBColorSpace;
                if (rotation) {
                    texture.rotation = rotation;
                    texture.center.set(0.5, 0.5);
                }
                if (!externalTextureRegistry[typeKey]) externalTextureRegistry[typeKey] = {};
                externalTextureRegistry[typeKey][mapType] = texture;
                clearTimeout(_textureRebuildTimer);
                // The only place this module reaches back into its host. An
                // external texture arrives asynchronously and the scene has to
                // be rebuilt to pick it up; HOW to rebuild is the host's
                // business, so it is a context callback rather than three more
                // page globals. Optional: an app that has no rebuild loop yet
                // (a headless export, say) simply does not pass one.
                _textureRebuildTimer = setTimeout(function () {
                    if (typeof ctx.onTextureLoaded === 'function') ctx.onTextureLoaded();
                }, 100);
            }, undefined, (err) => {
                console.warn(`Failed to load external texture ${typeKey}/${mapType}: ${url}`, err);
            });
        }

        
        function clearPartitionWallRenderCaches() {
            _partitionWallBodyGeometryCache.forEach(entry => {
                if (entry && entry.geometry) entry.geometry.dispose();
            });
            _partitionWallBodyGeometryCache.clear();
            _partitionWallProxyMaterialCache.forEach(material => {
                if (material) material.dispose();
            });
            _partitionWallProxyMaterialCache.clear();
        }

        /**
         * The real PBR texture set, from the app's `textures/` folder.
         *
         * Lifted out of the page so BOTH editors get the same surfaces. Without
         * it the procedural fallbacks are all that remain, and the rack tops and
         * floor vents render as flat white — which reads as "textures are
         * broken" rather than "the image set was never requested".
         *
         * `basePath` is a parameter because the two apps serve the folder from
         * different roots: the legacy page from `textures/` beside itself, the
         * React app through a Vite-served path.
         */
        function loadPbrTextureSet(basePath) {
            var base = basePath || 'textures';
            // objectKind -> folder. Several kinds share one folder on purpose:
            // rack-bottom and box reuse rack-top's horizontal brushed metal.
            var textureMap = {
                'rack-side':    'rack-side',
                'rack-front':   'rack-front',
                'rack-top':     'rack-top',
                'rack-bottom':  'rack-top',
                'server-front': 'server-front',
                // blank-panel is deliberately absent: it uses the procedural
                // _genBlankPanel (light smooth plastic) and an image would
                // replace a surface that is meant to look featureless.
                'floor':        'floor',
                'wall':         'wall',
                'baffle':       'baffle',
                'box':          'rack-top',
                'floor-vent':   'floor-vent',
                'acu-supply':   'acu-supply',
                'acu-return':   'acu-return'
            };
            var fileNames = { diffuse: 'color.jpg', normal: 'normal.jpg', roughness: 'roughness.jpg' };
            Object.keys(textureMap).forEach(function (objectKind) {
                Object.keys(fileNames).forEach(function (mapType) {
                    loadExternalTexture(objectKind, mapType,
                        base + '/' + textureMap[objectKind] + '/' + fileNames[mapType]);
                });
            });
            // The liquid-equipment sets (pipes, fittings, CDU, cold plates) live
            // in js/pbr_materials.js, which binds them to its materials as they
            // arrive. Requested here so both editors get them through the one
            // call each already makes — and only where textures are wanted.
            var lib = (typeof window !== 'undefined') ? window.pbrMaterials : null;
            if (lib && typeof lib.loadTextureSets === 'function') lib.loadTextureSets(base);
        }

        /**
         * The photographic surfaces: a server's rear socket panel, the
         * perforated outlet mesh a server patch is drawn with, the power
         * strips, the ACU return grille, the slotted baffle grille. These have
         * no procedural fallback (`_generateProceduralTexture` returns nulls
         * for every one of them), so an app that skips this call draws the
         * server outlet as a flat coloured rectangle and its patches as plain
         * tinted quads — which reads as "the textures are wrong" rather than
         * "these images were never requested". The table is the legacy page's
         * `_preloadImageTextures`; wrap mode is per image because the strips
         * and the grille are single photos and must not tile.
         */
        function loadPhotoTextureSet(basePath) {
            var base = (basePath || 'textures') + '/MISC/';
            var R = THREE.RepeatWrapping, C = THREE.ClampToEdgeWrapping;
            var photos = [
                ['server-front-photo', 'ServerFront_compressed.jpg', R],
                ['server-rear-socket', '1U_ServerRearSocket.jpg', R],
                ['server-outlet-mesh', 'sever_rear_outletMesh.jpg', R],
                ['rack-strip-left', 'Power Strip 1.jpg', C],
                ['rack-strip-right', 'Power Strip 2.jpg', C],
                ['acu-return-photo', 'ACU Return 3 Panel.png', C],
                ['baffle-grille', 'Slotted Grille - 600mm.jpg', R]
            ];
            photos.forEach(function (row) {
                loadExternalTexture(row[0], 'diffuse', base + row[1], 0, row[2]);
            });
        }

        // Moved here from geometry_pure.js: its default parameter
        // `meshList = renderMeshes` READS scene state, which the pure half
        // does not have. A checker that treated every identifier in the
        // parameter list as a local name missed it, and the export threw
        // `renderMeshes is not defined` the first time React ran it.

    
        function exportBoxModel(model, parts, isMirrored, meshList = ctx.renderMeshes) {
            const groups = {};
            meshList.forEach(mesh => {
                const data = mesh.userData || {};
                if (data.modelId !== model.uid || !data.exportName) return;
                if (data.isDoor) return; // doors exported as separate baffle STL files
                if (!groups[data.exportName]) groups[data.exportName] = [];
                groups[data.exportName].push(mesh);
            });
            Object.keys(groups).forEach(name => {
                parts.push(`solid ${name}\n`);
                groups[name].forEach(mesh => { appendMeshFacets(parts, mesh); });
                parts.push(`endsolid ${name}\n\n`);
            });
        }

        return {
            loadPbrTextureSet: loadPbrTextureSet,
            loadPhotoTextureSet: loadPhotoTextureSet,
            exportBoxModel: exportBoxModel,
            buildGroupedStlMap: buildGroupedStlMap,
            _buildSingleDoorStl: _buildSingleDoorStl,
            _withUnmergedModels: _withUnmergedModels,
            applyModelSolidOrientationOverrides: applyModelSolidOrientationOverrides,
            buildPartitionWallHoleZoneStlMap: buildPartitionWallHoleZoneStlMap,
            buildThroughZoneStl: buildThroughZoneStl,
            exportModelToStl: exportModelToStl,
            isModelEnabled: isModelEnabled,
            withExtraModelsRendered: withExtraModelsRendered,
            getRenderRootByModelId: getRenderRootByModelId,
            _mergeModelGroup: _mergeModelGroup,
            _unmergeModelGroup: _unmergeModelGroup,
            ensurePreviewVisibilityState: ensurePreviewVisibilityState,
            buildPartitionWallHoleZoneGeometry: buildPartitionWallHoleZoneGeometry,
            getPartitionWallProxyMaterial: getPartitionWallProxyMaterial,
            getModelWorldMatrix: getModelWorldMatrix,
            exportCustomModel: exportCustomModel,
            getModelById: getModelById,
            withTemporaryRenderedModels: withTemporaryRenderedModels,
            simCfgMerged: simCfgMerged,
            buildCustomPatchCutterGeometry: buildCustomPatchCutterGeometry,
            exportCustomSurfaceShellModel: exportCustomSurfaceShellModel,
            exportMeshByRegions: exportMeshByRegions,
            renderBaffleModel: renderBaffleModel,
            renderBoxModel: renderBoxModel,
            renderCustomModel: renderCustomModel,
            renderFloorModel: renderFloorModel,
            renderPartitionWallModel: renderPartitionWallModel,
            renderPipeModel: renderPipeModel,
            renderWallAcuModel: renderWallAcuModel,
            renderRackModel: renderRackModel,
            renderWallModel: renderWallModel,
            ensureCustomPatch3DDefaults: ensureCustomPatch3DDefaults,
            intersectPatchBodyWithRegionPlane: intersectPatchBodyWithRegionPlane,
            renderPlanarModelWithPatches: renderPlanarModelWithPatches,
            _buildSingleBoxFace: _buildSingleBoxFace,
            createMaterialForType: createMaterialForType,
            getAppliedCustomPatchFragments: getAppliedCustomPatchFragments,
            isLiquidMode: isLiquidMode,
            spawnRenderable: spawnRenderable,
            buildRackFrameGeometry: buildRackFrameGeometry,
            buildServerGeometry: buildServerGeometry,
            isRackDetailMode: isRackDetailMode,
            spawnPlanarRect: spawnPlanarRect,
            getRegionCenter: getRegionCenter,
            _getOrCreateTexture: _getOrCreateTexture,
            getCachedMaterial: getCachedMaterial,
            _buildSingleServerFace: _buildSingleServerFace,
            getRegionGeometryEntry: getRegionGeometryEntry,
            _buildServerFaceWithPatches: _buildServerFaceWithPatches,
            clearMaterialCache: clearMaterialCache,
            loadExternalTexture: loadExternalTexture,
            clearPartitionWallRenderCaches: clearPartitionWallRenderCaches
        };
    }

    return { create: createGeometryScene, getGroupedStlSourceCatalogue };
});
