/**
 * mesh_sizing.js — cell-size arithmetic for the mesh panel, shared by the
 * legacy and the React editor (each carries only the inputs / the button).
 *
 * Two jobs:
 *
 *  1. `describeMeshSizing(doc)` turns a meshConfig into what the mesher will
 *     actually build: the cell edge (mm) every refinement level resolves to,
 *     the thinnest fan-server slab and the level dcCart raises the rack
 *     region to for it (mirrors `_cartesian_rack_region_level` in
 *     stlgen_runner.py, including the `rackCellMinSize` cap), the first-layer
 *     floor in mm per class, and a cell budget from the floor area.
 *
 *  2. `sixSigmaMeshConfig(doc)` writes the 6Sigma sizing rule into a
 *     meshConfig patch. The rule is measured, not invented — see
 *     docs/reference/sixsigma-cell-size-rule.md: background 0.2-0.25 m
 *     whatever the room, 45-50 mm (about 1U) in rack / ACU regions, never
 *     finer than 17.5 mm, a first cell at every object surface of at least
 *     0.25-0.3 of the local spacing (10 mm absolute), 2:1 level jumps,
 *     about 7,100 cells per m² of floor.
 *
 * Pure functions over the V4 document: no DOM, no Three.js, no state
 * mutation. The caller applies the patch through its own setMeshConfig so
 * the undo checkpoint and the operation log stay where they belong.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.meshSizing = api;
}(typeof window !== 'undefined' ? window : this, function () {
    'use strict';

    // The 6Sigma numbers (Ref_code/6SigmaCases, room 301 VTU + three .room
    // Grid blocks). Lengths in metres.
    var SIXSIGMA = {
        maxCell: 0.25,          // Max_size_in_X/Y/Z
        minCell: 0.010,         // Minimum_Gap_Size: smaller cells are dropped by its solver
        rackCell: 0.050,        // 45 mm across the equipment bay, 50 mm along the depth, 1U = 44.45
        rackCellMin: 0.0175,    // finest cell in the whole 4.6M-cell grid (cabinet side zones)
        wallCell: 0.125,        // room walls see 100-250 mm
        firstLayerFraction: 0.3, // first cell >= 0.3 x local spacing (6Sigma: a full cell; Minimum_Wall_Distance_Fraction 0.1 on the centroid)
        firstLayerMax: 0.005,
        weldTolerance: 0.0012,
        aspectLimit: 5,
        cellsPerM2: 7100,       // 4,607,841 cells / 648.98 m²
        cellsPerCabinet: 16000,
        budgetMargin: 1.5
    };

    function num(v, fallback) {
        var n = Number(v);
        return Number.isFinite(n) ? n : fallback;
    }

    function cellAtLevel(base, level) {
        return base / Math.pow(2, Math.max(0, level | 0));
    }

    // Smallest level whose cell is <= target (never negative).
    function levelForCell(base, target) {
        if (!(base > 0) || !(target > 0)) return 0;
        return Math.max(0, Math.ceil(Math.log2(base / target) - 1e-9));
    }

    // Largest level whose cell is still >= minCell (the rackCellMinSize cap).
    function maxLevelForMinCell(base, minCell) {
        if (!(base > 0) || !(minCell > 0)) return Infinity;
        return Math.max(0, Math.floor(Math.log2(base / minCell) + 1e-9));
    }

    // Shoelace area of geometry.plane (XZ polygon); falls back to bounds.
    function floorArea(doc) {
        var g = (doc && doc.geometry) || {};
        var poly = Array.isArray(g.plane) ? g.plane : null;
        if (poly && poly.length >= 3) {
            var a = 0;
            for (var i = 0; i < poly.length; i++) {
                var p = poly[i], q = poly[(i + 1) % poly.length];
                a += num(p[0], 0) * num(q[1], 0) - num(q[0], 0) * num(p[1], 0);
            }
            return Math.abs(a) / 2;
        }
        var b = g.bounds || {};
        var dx = num(b.xMax && b.xMax.value, 0) - num(b.xMin && b.xMin.value, 0);
        var dz = num(b.zMax && b.zMax.value, 0) - num(b.zMin && b.zMin.value, 0);
        return Math.max(0, dx * dz);
    }

    function rackList(doc) {
        var racks = doc && doc.constructions && doc.constructions.racks;
        if (!racks) return [];
        return Array.isArray(racks) ? racks : Object.keys(racks).map(function (k) { return racks[k]; });
    }

    // Thinnest fan-server slab (m): slotHeight x slotOccupation over every
    // fan server. Mirrors `_min_server_slab_height` (stlgen_runner.py).
    function minFanServerSlab(doc) {
        var best = null;
        rackList(doc).forEach(function (rack) {
            if (!rack || !rack.geometry) return;
            var unit = num(rack.geometry.slotHeight, num(rack.geometry.unitHeight, 0.04445));
            var servers = rack.constructions && rack.constructions.servers;
            if (!servers) return;
            var list = Array.isArray(servers) ? servers : Object.keys(servers).map(function (k) { return servers[k]; });
            list.forEach(function (s) {
                if (!s || s.type !== 'fanServer') return;
                var span = num(s.geometry && s.geometry.slotOccupation, num(s.spanSlots, 1));
                var h = unit * Math.max(1, span);
                if (h > 0 && (best === null || h < best)) best = h;
            });
        });
        return best;
    }

    // The rack-region level dcCart will actually use: raised so the cell
    // resolves the thinnest fan server, capped by rackCellMinSize.
    function effectiveRackLevel(mc, doc) {
        var base = num(mc.meshBaseSize, 0.25);
        var requested = num(mc.rackRegionLevel, 3);
        var slab = minFanServerSlab(doc);
        var level = requested;
        var raisedFor = null;
        if (slab && slab > 0) {
            var need = levelForCell(base, slab);
            if (need > level) { level = need; raisedFor = slab; }
        }
        var minCell = num(mc.rackCellMinSize, 0);
        var capped = false;
        if (minCell > 0) {
            var cap = maxLevelForMinCell(base, minCell);
            if (level > cap && cap >= requested) { level = cap; capped = true; }
        }
        return { level: level, requested: requested, raisedFor: raisedFor, capped: capped, slab: slab };
    }

    function pair(v, d) {
        return Array.isArray(v) && v.length >= 2 ? [num(v[0], d), num(v[1], d)] : [d, d];
    }

    /**
     * Rows for the panel readout. Every row: { key, level, cellMm }.
     * Plus: budget { areaM2, recommendedCells, maxGlobalCells, cellsPerM2 },
     * firstLayer { fraction, maxMm, floorMmByClass }, rack { ... }.
     */
    function describeMeshSizing(doc) {
        var mc = (doc && doc.runDefaults && doc.runDefaults.meshConfig) || {};
        var base = num(mc.meshBaseSize, 0.25);
        var rack = effectiveRackLevel(mc, doc);
        var rows = [
            { key: 'background', level: 0, cellMm: base * 1000 },
            { key: 'room_wall', level: pair(mc.refinementLevel, 1)[1], cellMm: cellAtLevel(base, pair(mc.refinementLevel, 1)[1]) * 1000 },
            { key: 'rack_region', level: rack.level, cellMm: cellAtLevel(base, rack.level) * 1000 },
            { key: 'acu', level: pair(mc.acuRefinementLevel, 1)[1], cellMm: cellAtLevel(base, pair(mc.acuRefinementLevel, 1)[1]) * 1000 },
            { key: 'white_model', level: pair(mc.boxRefinementLevel, 1)[1], cellMm: cellAtLevel(base, pair(mc.boxRefinementLevel, 1)[1]) * 1000 },
            { key: 'baffle_floor', level: pair(mc.wallBafflesRefinementLevel, 1)[1], cellMm: cellAtLevel(base, pair(mc.wallBafflesRefinementLevel, 1)[1]) * 1000 }
        ];
        var fraction = num(mc.firstLayerMinFraction, 0.3);
        var maxM = num(mc.firstLayerMax, 0.005);
        var weld = num(mc.weldTolerance, 0.0012);
        var floorMm = {};
        rows.forEach(function (r) {
            var h = r.cellMm / 1000;
            floorMm[r.key] = Math.max(weld, Math.min(fraction * h, maxM > 0 ? maxM : Infinity)) * 1000;
        });
        var area = floorArea(doc);
        var recommended = Math.ceil(area * SIXSIGMA.cellsPerM2 * SIXSIGMA.budgetMargin / 1e5) * 1e5;
        return {
            base: base,
            rows: rows,
            rack: rack,
            firstLayer: { fraction: fraction, maxMm: maxM * 1000, weldMm: weld * 1000, floorMmByClass: floorMm },
            budget: {
                areaM2: area,
                racks: rackList(doc).length,
                recommendedCells: Math.max(500000, recommended),
                maxGlobalCells: num(mc.maxGlobalCells, 0),
                cellsPerM2: SIXSIGMA.cellsPerM2
            },
            finestMm: Math.min.apply(null, rows.map(function (r) { return r.cellMm; })),
            sixSigma: SIXSIGMA
        };
    }

    /**
     * The 6Sigma rule as a meshConfig patch. Only sizing keys; engine,
     * parallelism, locationInMesh and zones are the user's.
     * Returns { patch, notes } where notes are [{ key, from, to }].
     */
    function sixSigmaMeshConfig(doc) {
        var mc = (doc && doc.runDefaults && doc.runDefaults.meshConfig) || {};
        var current = num(mc.meshBaseSize, 0.25);
        // 6Sigma's background is 0.25 m whatever the room; a finer explicit
        // choice (DAML 0.2) is kept, a coarser one (0.34) is pulled down.
        var base = current > SIXSIGMA.maxCell ? SIXSIGMA.maxCell : current;
        // The rule is a FLOOR on refinement, never a ceiling: 6Sigma adds
        // cells in gaps on top of its 45-50 mm rack pitch (1.17M
        // Cells_Added_In_Gaps in room 301), which this rule does not model,
        // so a level the project already asked for is kept. Base_leakage
        // (thin leakage slots) diverged at the 42 mm cell the pitch alone
        // gives and solves at the 21 mm its author set.
        var rackLevel = Math.max(
            levelForCell(base, SIXSIGMA.rackCell),                  // 0.25 -> 3 (31 mm), 0.2 -> 2 (50 mm)
            num(mc.rackRegionLevel, 0) | 0
        );
        var sub = Math.max(1, rackLevel - 1);
        var keep = function (key, proposed) {
            var cur = pair(mc[key], 0);
            return [Math.max(cur[0], proposed[0]), Math.max(cur[1], proposed[1])];
        };
        var keepScalar = function (key, proposed) { return Math.max(num(mc[key], 0) | 0, proposed); };
        var area = floorArea(doc);
        var budget = Math.max(500000, Math.ceil(area * SIXSIGMA.cellsPerM2 * SIXSIGMA.budgetMargin / 1e5) * 1e5);
        var patch = {
            meshBaseSize: base,
            meshDetectBaseSize: null,
            rackRegionLevel: rackLevel,
            rackCellMinSize: SIXSIGMA.rackCellMin,
            refinementLevel: keep('refinementLevel', [1, Math.max(1, levelForCell(base, SIXSIGMA.wallCell))]),
            acuRefinementLevel: keep('acuRefinementLevel', [sub, rackLevel]),
            boxRefinementLevel: keep('boxRefinementLevel', [1, sub]),
            partitionWallRefinementLevel: keep('partitionWallRefinementLevel', [sub, rackLevel]),
            wallBafflesRefinementLevel: keep('wallBafflesRefinementLevel', [sub, rackLevel]),
            floorRefinementLevel: keep('floorRefinementLevel', [1, sub]),
            defaultFeatureLevel: keepScalar('defaultFeatureLevel', rackLevel),
            acuFeatureLevel: keepScalar('acuFeatureLevel', rackLevel),
            boxFeatureLevel: keepScalar('boxFeatureLevel', rackLevel),
            partitionWallFeatureLevel: keepScalar('partitionWallFeatureLevel', rackLevel),
            baffleFeatureLevel: keepScalar('baffleFeatureLevel', rackLevel),
            floorFeatureLevel: keepScalar('floorFeatureLevel', rackLevel),
            firstCellSize: null,
            weldTolerance: SIXSIGMA.weldTolerance,
            firstLayerMinFraction: SIXSIGMA.firstLayerFraction,
            firstLayerMax: SIXSIGMA.firstLayerMax,
            firstLayerSealGap: -1,
            maxGlobalCells: Math.max(budget, num(mc.maxGlobalCells, 0) | 0)
        };
        var notes = [];
        Object.keys(patch).forEach(function (k) {
            var from = mc[k], to = patch[k];
            if (JSON.stringify(from === undefined ? null : from) !== JSON.stringify(to)) {
                notes.push({ key: k, from: from === undefined ? null : from, to: to });
            }
        });
        return { patch: patch, notes: notes, areaM2: area };
    }

    // Production preset: keep requested local refinement and geometry intact.
    // The bounded cut-cell path does not multiply global refinement surfaces.
    function dcCartProductMeshConfig(doc) {
        var mc = (doc && doc.runDefaults && doc.runDefaults.meshConfig) || {};
        var patch = {
            meshBaseSize: 0.25, meshEngine: 'dcCart', parallelMesh: true, meshProcess: 8,
            meshTimeLimitSeconds: 300, weldTolerance: 0.0012,
            firstLayerMinFraction: 0.3, firstLayerMax: 0.005, firstLayerSealGap: -1,
            rackCellMinSize: 0.0175
        };
        var notes = [];
        Object.keys(patch).forEach(function (key) {
            if (JSON.stringify(mc[key]) !== JSON.stringify(patch[key])) {
                notes.push({key: key, from: mc[key] === undefined ? null : mc[key], to: patch[key]});
            }
        });
        return {patch: patch, notes: notes, areaM2: floorArea(doc)};
    }

    return {
        dcCartProductMeshConfig: dcCartProductMeshConfig,
        SIXSIGMA: SIXSIGMA,
        cellAtLevel: cellAtLevel,
        levelForCell: levelForCell,
        maxLevelForMinCell: maxLevelForMinCell,
        floorArea: floorArea,
        minFanServerSlab: minFanServerSlab,
        effectiveRackLevel: effectiveRackLevel,
        describeMeshSizing: describeMeshSizing,
        sixSigmaMeshConfig: sixSigmaMeshConfig
    };
}));
