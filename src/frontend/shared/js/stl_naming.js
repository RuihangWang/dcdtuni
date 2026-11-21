/**
 * stl_naming.js — which OpenFOAM patch each exported solid becomes.
 *
 * This is the naming and grouping layer of the STL export: it decides the
 * `solid <name>` written into each file and which merged file a rack's solids
 * are routed into. It is PURE — model data in, strings out, no Three.js scene
 * and no module-scoped state — which is why it can be shared between the two
 * editors while the geometry builder above it cannot yet.
 *
 * Getting a name wrong here is not a rendering bug. The solid name IS the
 * OpenFOAM patch name, so a rename silently moves a boundary condition onto a
 * different surface, or drops it: the runner's mesh-acceptance gate checks that
 * every region declared in snappyHexMeshDict/refinementSurfaces exists in
 * polyMesh/boundary, and a solid routed to the wrong bucket passes that gate
 * while heating the wrong wall.
 *
 * Lifted verbatim from STL_generate.html. `parity/stl_oracle.py` compares
 * exports byte for byte, and the solid names are part of what it compares.
 *
 * Public API — see each function's own comment:
 *   sanitizeName, getFaceDisplayName, usesSyntheticSingleFaceExportName,
 *   composeModelScopedExportName, getPatchFinalExportName,
 *   getRackServerViews, getRackEquipmentEndUnit, getRackServerSlotRangeLabel,
 *   getRackEquipmentExportStem,
 *   getRackThroughZoneGroups, computeServerBucketKeyForSolid,
 *   classifyRackSolidToBucketFile
 *
 * `computeServerBucketKeyForSolid` falls back to `(function(){var m=(typeof window!=='undefined')?window.cfdprojStateV4:null;var st=(typeof window!=='undefined')?window.state:null;return (m&&st)?m.allModels(st):[];})()` when no
 * owning model is passed. That fallback is the one impure edge and it is
 * deliberate: it preserves the legacy behaviour for any caller that does not
 * know the owner. Pass the owner and the module is pure.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.stlNaming = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    // Was a module-level const in STL_generate.html. It gates the rack
    // through-zone feature, and the naming helpers read it — so it has to move
    // WITH them or `getRackThroughZoneGroups` throws the moment this module is
    // used outside that page.
    var ENABLE_RACK_THROUGH_ZONE = true;

    // js/rack_state.js — the rack model's own normalisation helpers. Bound
    // explicitly for the same reason as the other siblings: resolving them as
    // bare globals only worked inside the legacy page, where a top-level
    // `function` becomes a window property.
    function _rack() {
        var m = (typeof window !== 'undefined') ? window.rackState : null;
        if (!m) throw new Error('stl_naming.js needs js/rack_state.js loaded first');
        return m;
    }
    function _rackServerView() { return _rack()._rackServerView.apply(null, arguments); }

    function sanitizeName(text) {
        return (text || 'model').replace(/[^a-zA-Z0-9_]+/g, '_');
    }


    function getFaceDisplayName(face) {
        return face.displayName || face.label;
    }

    function usesSyntheticSingleFaceExportName(model) {
        return !!model && (model.kind === 'wall' || model.kind === 'floor' || model.kind === 'baffle');
    }


    function composeModelScopedExportName(model, faceId, face, opts) {
        const modelPrefix = sanitizeName(model.meta.name);
        const facePrefix = usesSyntheticSingleFaceExportName(model)
            ? ''
            : sanitizeName(getFaceDisplayName(face) || faceId || 'face');
        const joinName = (...parts) => sanitizeName(parts.filter(Boolean).join('_'));
        if (opts && opts.isWallPart) {
            // 2026-04-19: previously returned bare modelName when face had no
            // patches, which collided when wallPoly split into multiple pieces
            // via `cutouts` (delete-patch-with-cavity / partition wall holes).
            // Suffix when >1 part so the exported STL has unique `solid` names.
            const hasPatches = face.patches && face.patches.length > 0;
            const partCount = Number.isFinite(opts.partCount) ? opts.partCount : (hasPatches ? 2 : 1);
            if (hasPatches || partCount > 1) {
                return joinName(modelPrefix, facePrefix, `part${opts.partIndex + 1}`);
            }
            return joinName(modelPrefix, facePrefix);
        }
        if (opts && opts.isPatch) {
            // Numbered patches: single → _patch, multiple → _patch_1, _patch_2
            const suffix = opts.patchCount === 1 ? '' : `_${opts.patchIndex + 1}`;
            return joinName(modelPrefix, facePrefix, `patch${suffix}`);
        }
        // Legacy fallback: use baseName directly
        const baseName = opts && opts.baseName ? sanitizeName(opts.baseName) : 'patch';
        return joinName(modelPrefix, facePrefix, baseName);
    }


    function getPatchFinalExportName(model, faceId, patch) {
        const face = model.geometry.faces[faceId];
        const patches = face ? (face.patches || []) : [];
        const patchIndex = patches.findIndex(p => p.id === patch.id);
        const idx = patchIndex >= 0 ? patchIndex : 0;
        return composeModelScopedExportName(model, faceId, face, {
            isPatch: true, patchIndex: idx, patchCount: patches.length || 1
        });
    }

    function _rackServersDict(model) {
        if (!model || model.kind !== 'rack') return null;
        const cons = model.constructions;
        if (!cons || !cons.servers || typeof cons.servers !== 'object') return null;
        return cons.servers;
    }

    function getRackServerViews(model) {
        const servers = _rackServersDict(model);
        if (!servers) return [];
        return Object.keys(servers).map(uid => _rackServerView(servers, uid));
    }


    function getRackEquipmentEndUnit(equipment) {
        return equipment ? equipment.startUnit + equipment.spanUnits - 1 : -1;
    }


    /**
     * "U33 ~ U34" — the U range a rack server covers, 1-based and inclusive, in
     * the exact form both editors' server inspectors print. '' when the rack has
     * no server with that id or its start slot is not a number; a span below
     * one U reads as 1U.
     */
    function getRackServerSlotRangeLabel(model, equipmentId) {
        const view = getRackServerViews(model).find(v => v && v.id === equipmentId);
        if (!view || !Number.isFinite(view.startUnit)) return '';
        const start = view.startUnit;
        const span = Number(view.spanUnits);
        const end = start + (Number.isFinite(span) ? Math.max(1, span) : 1) - 1;
        return `U${start + 1} ~ U${end + 1}`;
    }


    /**
     * The U range a server view covers, as one comparable string. A view whose
     * span is not a number reads as a single U, the convention
     * getRackServerSlotRangeLabel already uses. Compared as a key rather than by
     * identity because getRackServerViews builds a fresh proxy per call: the view
     * a caller holds is never `===` the one this function looks at.
     */
    function _uRangeKey(equipment) {
        const start = Number.isFinite(equipment.startUnit) ? equipment.startUnit : 0;
        const end = getRackEquipmentEndUnit(equipment);
        return `${start}:${Number.isFinite(end) ? end : start}`;
    }

    function getRackEquipmentExportStem(model, equipment) {
        const rName = sanitizeName(model.meta.name);
        const eqName = sanitizeName(equipment.name || equipment.id);
        const baseStem = sanitizeName(`${rName}_${eqName}`);
        const equipments = getRackServerViews(model);
        const sameName = equipments.filter(item =>
            sanitizeName(item.name || item.id) === eqName
        );
        if (sameName.length <= 1) return baseStem;
        const startUnit = Number.isFinite(equipment.startUnit) ? equipment.startUnit : 0;
        const endUnit = getRackEquipmentEndUnit(equipment);
        const uStem = endUnit > startUnit
            ? sanitizeName(`${baseStem}_u${startUnit + 1}_u${endUnit + 1}`)
            : sanitizeName(`${baseStem}_u${startUnit + 1}`);
        // Stacked servers (2026-09-17 spec §8): two records may share a name AND a
        // U range, differing only in depth, and then the U suffix separates
        // nothing. A shared stem merges both solids into one STL body, the
        // backend's uniqueness validator rejects the case, and the BC registry
        // overwrites one server's patches with the other's — so the inlet setback
        // in whole millimetres joins the stem. Servers whose U ranges differ keep
        // the stem they have always had.
        const rangeKey = _uRangeKey(equipment);
        const sameRangeCount = sameName.filter(item => _uRangeKey(item) === rangeKey).length;
        if (sameRangeCount <= 1) return uStem;
        const inletMm = Math.round((Number(equipment.inletOffset) || 0) * 1000);
        return sanitizeName(`${uStem}_z${inletMm}`);
    }

        function _maxContigSpan(arr, startU, endU) {
            var maxSpan = 0, cur = 0;
            for (var i = startU; i <= endU; i++) {
                if (arr[i]) { cur++; if (cur > maxSpan) maxSpan = cur; }
                else cur = 0;
            }
            return maxSpan;
        }


    function getRackThroughZoneGroups(model) {
        // Returns [{startUnit, endUnit, sigma}] for each contiguous range of gap-slots
        // that contains at least one through-slot (both front AND back deleted).
        // The range spans the full extent of gaps on EITHER side, so a 4U front gap
        // + 1U back gap at the same height produces a 4U leakage zone.
        if (!ENABLE_RACK_THROUGH_ZONE) return [];
        if (!model || model.kind !== 'rack') return [];
        const df = model.deletedFaces;
        const sigma = model.throughSlotSigma || {};
        const unitCount = model.geometry.slot;

        // Step 1: find consecutive ranges where either front or back is deleted
        const candidates = [];
        let start = -1;
        for (let i = 0; i < unitCount; i++) {
            const hasGap = !!(df.front[i] || df.back[i]);
            if (hasGap && start < 0) { start = i; }
            else if (!hasGap && start >= 0) {
                candidates.push([start, i - 1]);
                start = -1;
            }
        }
        if (start >= 0) candidates.push([start, unitCount - 1]);

        // Step 2: keep only ranges that contain at least one through-slot (both sides deleted)
        function _maxContigSpan(arr, startU, endU) {
            var maxSpan = 0, cur = 0;
            for (var i = startU; i <= endU; i++) {
                if (arr[i]) { cur++; if (cur > maxSpan) maxSpan = cur; }
                else cur = 0;
            }
            return maxSpan;
        }
        const groups = [];
        candidates.forEach(function(range) {
            var hasThrough = false;
            for (var i = range[0]; i <= range[1]; i++) {
                if (df.front[i] && df.back[i]) { hasThrough = true; break; }
            }
            if (hasThrough) {
                // 2026-04-18: bucket key = min(front_contig_span, back_contig_span)
                // within the zone range. The physical thinness snappy must
                // resolve is set by whichever side has the narrowest opening,
                // not by the total range span. Example: 5U gap range with
                // front having a single 1U hole → 1U-level refinement is the
                // minimum needed to keep that 1U feature from being merged.
                var frontSpan = _maxContigSpan(df.front, range[0], range[1]);
                var backSpan = _maxContigSpan(df.back, range[0], range[1]);
                groups.push({
                    startUnit: range[0],
                    endUnit: range[1],
                    sigma: sigma[range[0]] != null ? sigma[range[0]] : 0.05,
                    minSideSpan: Math.min(frontSpan, backSpan)
                });
            }
        });
        return groups;
    }

    function _computeServerBucketKeyForSolid(solidName, ownerModel) {
        if (!solidName) return null;
        var hit = null;
        var hitStemLen = -1;
        // Server export stems are model-scoped (they embed the sanitized model
        // name), so a solid emitted by rack A can only prefix-match rack A's own
        // server stems. When the owning model is known (the only caller passes
        // it), scan just that rack — identical longest-prefix result, but O(own
        // servers) instead of O(all racks × servers). The global fallback (no
        // ownerModel) preserves the original behaviour for any other caller.
        var scan = ownerModel ? [ownerModel] : ((function(){var m=(typeof window!=='undefined')?window.cfdprojStateV4:null;var st=(typeof window!=='undefined')?window.state:null;return (m&&st)?m.allModels(st):[];})() || []);
        scan.forEach(function(model) {
            if (model.kind !== 'rack') return;
            getRackServerViews(model).forEach(function(eq) {
                if (!eq) return;
                var stem = getRackEquipmentExportStem(model, eq);
                if (!stem) return;
                if (solidName.indexOf(stem + '_') === 0 && stem.length > hitStemLen) {
                    hit = String(eq.spanUnits || 1);
                    hitStemLen = stem.length;
                }
            });
        });
        return hit;
    }

    /**
     * True when `solidName` is one of a server's LEFT/RIGHT interface-wall
     * solids (WP-W6): `{stem}_left` / `{stem}_right`, or a patch/wall
     * subdivision of them (`{stem}_left_p0`, `{stem}_right_wall`, ...).
     *
     * Same longest-stem prefix match as _computeServerBucketKeyForSolid, so a
     * frame solid (`..._frame_outer_left` — caught earlier anyway) or a
     * blanking panel that merely ENDS in `_left` never qualifies: the name has
     * to start with one of THIS rack's server export stems, and the remainder
     * has to be exactly a `left`/`right` face suffix of
     * getServerFaceExportName (bare, `_p<N>`, or `_wall`).
     */
    function _isServerInterfaceWallSolid(solidName, model) {
        if (!solidName || !model || model.kind !== 'rack') return false;
        var rest = null;
        var hitStemLen = -1;
        getRackServerViews(model).forEach(function(eq) {
            if (!eq) return;
            var stem = getRackEquipmentExportStem(model, eq);
            if (!stem) return;
            if (solidName.indexOf(stem + '_') === 0 && stem.length > hitStemLen) {
                rest = solidName.slice(stem.length + 1);
                hitStemLen = stem.length;
            }
        });
        return rest !== null && /^(?:left|right)(?:_|$)/.test(rest);
    }

    // meshConfig.blankingPanelPatches = 'perRack': every rack's blanking panels are their own
    // bucket (merged_<kind>BlankingPanel_<rack>_<N>U.stl), so each rack has its own patch pair and
    // its own leakage coefficient. Default ('perSpan'): one bucket per U span for all racks.
    function _blankingBucketOptions(project) {
        var mc = project && project.runDefaults && project.runDefaults.meshConfig;
        return { blankingPerRack: !!(mc && mc.blankingPanelPatches === 'perRack') };
    }

    function _classifyRackSolidToBucketFile(solidName, model, opts) {
        if (!solidName || !model || model.kind !== 'rack') return null;
        var n = solidName;
        // Frame solids: handled by the scene-level rack_frame export group.
        // Matches both the legacy per-subregion names (`{rack}_frame_outer_left`,
        // etc. — infix `_frame_`) and the merged single-solid name
        // (`{rack}_frame` — suffix form, see MERGE_RACK_FRAME_SOLIDS).
        if (n.indexOf('_frame_') !== -1 || /_frame$/.test(n)) return null;
        // Partition 隔板 (check BEFORE server/blanking/wallBaffle: partition
        // names end in `_partition` (no `_inside`, no U-range). Each partition
        // is a single 1U horizontal divider sitting between U-slot i and i+1.
        // Emitted by getRackPartitionExportName as `{rack}_u{idx+1}_partition`.
        var mPart = n.match(/_u(\d+)_partition$/);
        if (mPart) {
            // Partitions are always 1U thin baffles (horizontal dividers
            // between two adjacent U-slots), so bucket key is fixed "1".
            return 'merged_partition_1U.stl';
        }
        // Server left/right INTERFACE WALLS (WP-W6, 2026-09-18): the scene
        // builder emits the server's left/right faces again so a server is a
        // closed box in the viewport and in the manifold-patch STL export.
        // They coincide with the rack frame's inner cavity walls, so they must
        // NOT share the fluid-face bucket (merged_server_<N>U.stl) — they route
        // to their OWN file, which the cfdrun manifest / boundaryBindings never
        // enumerate (cfdrun_build.js _buildServerFacesExport keeps four faces).
        // Checked BEFORE the U-span server bucket below, whose stem-prefix
        // match would otherwise absorb them.
        if (_isServerInterfaceWallSolid(solidName, model)) return 'merged_server_interface_walls.stl';
        // Detect owning rack's blankingPanelType for porous routing.
        // typicalSmallGap and fullyOpen are both cyclic baffles (σ 0.6401 / 1).
        var _blankType = model.cooling.blankingPanelType || 'typicalSmallGap';
        var _isPorous = _blankType === 'typicalSmallGap' || _blankType === 'fullyOpen';
        // Server face solids (fanServer + nonFan). Pass the owning model so the
        // stem search stays O(this rack's servers), not O(all racks × servers).
        var serverKey = _computeServerBucketKeyForSolid(solidName, model);
        if (serverKey !== null) return 'merged_server_' + serverKey + 'U.stl';
        // Blanking panels: blankingPanelType determines the bucket for ALL panels.
        // typicalSmallGap, fullyOpen → merged_perforatedBlankingPanel_<N>U.stl
        // fullBlock       → merged_wallBlankingPanel_<N>U.stl
        var _perRack = !!(opts && opts.blankingPerRack);
        var mSlotRange = n.match(/_(?:inlet|outlet)_u(\d+)_u(\d+)(?:_|$)/);
        if (mSlotRange) {
            var span = parseInt(mSlotRange[2]) - parseInt(mSlotRange[1]) + 1;
            return (_isPorous ? 'merged_perforatedBlankingPanel_' : 'merged_wallBlankingPanel_')
                + (_perRack ? n.slice(0, mSlotRange.index) + '_' : '') + span + 'U.stl';
        }
        var mSlotSingle = n.match(/_(?:inlet|outlet)_u(\d+)(?:_|$)/);
        if (mSlotSingle) {
            return (_isPorous ? 'merged_perforatedBlankingPanel_' : 'merged_wallBlankingPanel_')
                + (_perRack ? n.slice(0, mSlotSingle.index) + '_' : '') + '1U.stl';
        }
        console.warn('[stl-bucket] unrouted rack solid:', solidName);
        return null;
    }

    return {
        sanitizeName: sanitizeName,
        getFaceDisplayName: getFaceDisplayName,
        usesSyntheticSingleFaceExportName: usesSyntheticSingleFaceExportName,
        composeModelScopedExportName: composeModelScopedExportName,
        getPatchFinalExportName: getPatchFinalExportName,
        getRackServerViews: getRackServerViews,
        getRackEquipmentEndUnit: getRackEquipmentEndUnit,
        getRackServerSlotRangeLabel: getRackServerSlotRangeLabel,
        getRackEquipmentExportStem: getRackEquipmentExportStem,
        getRackThroughZoneGroups: getRackThroughZoneGroups,
        computeServerBucketKeyForSolid: _computeServerBucketKeyForSolid,
        classifyRackSolidToBucketFile: _classifyRackSolidToBucketFile,
        blankingBucketOptions: _blankingBucketOptions,
        // Exported for geometry_pure.js, which reads the same normalised
        // servers dict. It lives here because the export-name helpers need it.
        rackServersDict: _rackServersDict,
        ENABLE_RACK_THROUGH_ZONE: ENABLE_RACK_THROUGH_ZONE
    };
});
