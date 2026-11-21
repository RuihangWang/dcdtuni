// cfdproj_state_v4.js — V4 editor state factory (single source of truth, Plan 2 Task 1).
// Public API (window.cfdprojStateV4 / module.exports):
//   getInitialStateV4() -> fresh V4-shaped editor state (no ui key, no construction array)
// Depends on: cfdproj_schema.js (must be loaded first in the browser).
(function (root, factory) {
  const mod = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = mod;
  if (typeof window !== 'undefined') window.cfdprojStateV4 = mod;
})(this, function () {
  'use strict';

  const S = (typeof require !== 'undefined') ? require('./cfdproj_schema.js')
            : (typeof window !== 'undefined' ? window.cfdprojSchema : null);

  // Migrate module: in node it's the CommonJS export; in the browser the migrate
  // functions are attached directly to `window` (so MIG.migrateModelV3toV4 resolves
  // to window.migrateModelV3toV4). Acquired the same dual way as `S` above.
  const MIG = (typeof require !== 'undefined') ? require('./cfdproj_migrate.js')
              : (typeof window !== 'undefined' ? window : null);

  // Mesh-configuration defaults — keys from getInitialState().simulation partitioned
  // by S.MESH_KEYS. Keep in the same order as getInitialState().simulation for
  // easy diff audits (test_cfdproj_schema_keysplit.py reconciles this list).
  const DEFAULT_MESH_CONFIG = {
    locationInMesh:             { x: 5.0, y: 4.0, z: 1.5 },
    locInMeshVisible:           true,
    meshBaseSize:               0.25,
    meshEngine:                 'dcCart',
    meshTimeLimitSeconds:       300,
    weldTolerance:             0.0012,
    firstLayerMinFraction:     0.3,
    firstLayerMax:             0.005,
    firstLayerSealGap:         -1,
    rackCellMinSize:           0.0175,
    meshDetectBaseSize:         null,
    maxGlobalCells:             5000000,
    blockMeshBounds:            null,
    blockMeshEditActive:        false,
    blockMeshMoveMode:          false,
    refinementLevel:            [1, 1],
    rackRegionLevel:            3,
    acuRefinementLevel:         [1, 1],
    boxRefinementLevel:         [1, 1],
    wallBafflesRefinementLevel: [1, 1],
    floorRefinementLevel:       [1, 1],
    floorFeatureLevel:          2,
    defaultFeatureLevel:        2,
    acuFeatureLevel:            2,
    boxFeatureLevel:            2,
    baffleFeatureLevel:         2,
    parallelMesh:               true,
    meshProcess:                8,
    refinementZones:            [],
  };

  // Simulation-configuration defaults — keys from getInitialState().simulation
  // partitioned by S.SIM_KEYS.
  const DEFAULT_SIM_CONFIG = {
    endTime:                500,
    writeInterval:          100,
    doublePrecisionWrite:   false,
    writePrecision:         'single',
    endTimeSeconds:         300,
    deltaT:                 0.1,
    maxDeltaT:              1.0,
    maxCo:                  1.0,
    writeIntervalSeconds:   10,
    adjustTimeStep:         true,
    startFrom:              'latestTime',
    // E8c: a transient step iterates until transientResidualControl is met, at most this often.
    nOuterCorrectors:       20,
    nCorrectors:            2,
    ddtScheme:              'Euler',
    crankNicolsonCoeff:     0.9,
    solverType:             'steady',
    gravityX:               0,
    gravityY:               -9.81,
    gravityZ:               0,
    parallelSolve:          true,
    solveProcess:           8,
    residualControl:        { p_rgh: 1e-4, U: 1e-5, T: 1e-5, k_epsilon: 1e-4 },
    transientResidualControl: { p_rgh: 1e-3, U: 1e-4, T: 1e-4, k_epsilon: 1e-4 },
    solverPreset:           'fast',
    nonOrthoCorrection:     false,
    schemes: {
      divSchemeU:           'bounded Gauss upwind',
      divSchemeT:           'bounded Gauss upwind',
      divSchemeK:           'bounded Gauss upwind',
      divSchemeEpsilon:     'bounded Gauss upwind',
      gradSchemeDefault:    'Gauss linear',
      gradSchemeU:          '',
      gradSchemeK:          '',
      gradSchemeEpsilon:    '',
      laplacianScheme:      'Gauss linear limited 0.5',
      snGradScheme:         'limited 0.5',
    },
    relaxationFactors:      { p_rgh: 0.3, U: 0.7, T: 0.7, k_epsilon: 0.7 },
    nNonOrthogonalCorrectors: 0,
  };

  // Returns a fresh V4-shaped editor state:
  //   - schemaVersion: 4
  //   - V4 constructions buckets (uid-keyed objects, not an array)
  //   - runDefaults with meshConfig / simulationConfig / liquid
  //   - meta defaults matching legacy getInitialState() (exportFileName:'default', inputUnit:'mm')
  //   - geometry defaults (height:3.6, temperature:22)
  //   - NO ui key, NO construction array
  function getInitialStateV4() {
    const doc = S.emptyDocument();

    // Override meta defaults to match legacy getInitialState() values.
    doc.meta.exportFileName = 'default';
    doc.meta.inputUnit = 'mm';

    // Override geometry defaults to match legacy getInitialState().meta.room values.
    doc.geometry.height = 3.6;
    doc.geometry.temperature = 22;

    // Populate runDefaults from the partitioned simulation defaults.
    doc.runDefaults.meshConfig = Object.assign({}, DEFAULT_MESH_CONFIG);
    doc.runDefaults.simulationConfig = Object.assign({}, DEFAULT_SIM_CONFIG);
    // liquid is already seeded by emptyDocument(); keep it explicit for clarity.
    doc.runDefaults.liquid = { enabled: false, solver: {}, inheritResults: false };

    return doc;
  }

  // New projects and missing fields use the bounded dcCart production preset.
  // Existing explicit local refinement is retained by the sizing function.
  function _meshSizing() {
    if (typeof require !== 'undefined') { try { return require('./mesh_sizing.js'); } catch (e) { /* browser bundle */ } }
    return (typeof window !== 'undefined' && window.meshSizing) || null;
  }

  function applyNewProjectSizing(doc) {
    const sizing = _meshSizing();
    if (!sizing || !doc || !doc.runDefaults) return doc;
    const rd = doc.runDefaults;
    rd.meshConfig = rd.meshConfig || {};
    rd.simulationConfig = rd.simulationConfig || {};
    const meshKeys = new Set(S.MESH_KEYS);
    const patch = sizing.dcCartProductMeshConfig(doc).patch;
    for (const k in patch) (meshKeys.has(k) ? rd.meshConfig : rd.simulationConfig)[k] = patch[k];
    return doc;
  }

  function newDocumentV4() {
    return applyNewProjectSizing(getInitialStateV4());
  }

  const BUCKET_BY_KIND = S.BUCKET_BY_KIND;
  const CONSTRUCTION_BUCKETS = S.BUCKETS.filter(b => b !== 'liquidFlowNetworks');

  function bucketForKind(kind) { return BUCKET_BY_KIND[kind] || 'boxes'; }

  function allModels(st) {
    const out = [];
    for (const b of CONSTRUCTION_BUCKETS) {
      const d = st.constructions[b];
      if (d) for (const k in d) out.push(d[k]);
    }
    return out;
  }

  function addConstruction(st, m) { st.constructions[bucketForKind(m.kind)][m.uid] = m; return m; }

  function findConstruction(st, uid) {
    for (const b of CONSTRUCTION_BUCKETS) {
      const d = st.constructions[b];
      if (d && uid in d) return d[uid];
    }
    return null;
  }

  function removeConstruction(st, uid) {
    for (const b of CONSTRUCTION_BUCKETS) {
      const d = st.constructions[b];
      if (d && uid in d) { delete d[uid]; return true; }
    }
    return false;
  }

  function clearConstructions(st) {
    for (const b of CONSTRUCTION_BUCKETS) st.constructions[b] = {};
  }


  // finalizeNewModelV4(v3seed): convert a v3-shaped factory seed into a ready-to-use
  // in-memory V4 model, identical in shape to a model produced by the load path
  // (migrate -> non-enumerable working kind). Factories build a v3 seed and call this so
  // editor state holds native V4 objects.
  //   - MIG.migrateModelV3toV4 mirrors the load migrator (byte-identical output).
  //   - kind is the v3 seed kind (the correct working discriminator, e.g. 'heat_source'
  //     for a box-subtype) and is NON-enumerable, matching normalizeStateV4 — so it
  //     never re-serializes into the saved .cfdproj (V4 derives kind from the bucket).
  function finalizeNewModelV4(v3seed) {
    const v4 = MIG.migrateModelV3toV4(v3seed);
    Object.defineProperty(v4, 'kind', { value: v3seed.kind, enumerable: false, configurable: true, writable: true });
    return v4;
  }

  // Canonical kind per construction bucket, derived from the schema's BUCKET_BY_KIND.
  // Box-subtypes (heat_source/crh/cold_plate) carry meta.legacyKind; every other kind is
  // the sole/primary kind for its bucket, so inverting BUCKET_BY_KIND (skipping the boxes
  // subtypes) yields the canonical kind to restore when meta.legacyKind is absent.
  function _canonicalKindByBucket() {
    const map = {};
    for (const kind in BUCKET_BY_KIND) {
      const bucket = BUCKET_BY_KIND[kind];
      if (bucket === 'boxes' && kind !== 'box') continue;
      map[bucket] = kind;
    }
    return map;
  }

  // E8c: a project saved before transient steps converged ran a fixed one or two
  // PIMPLE outer correctors with no residual control. It gets the per-step set and
  // the per-step maximum once (the marker is the missing set); after that the
  // values are the user's. In place; returns whether it changed anything. The
  // EDITOR's load path calls it (projectStore.loadProject); normalizeStateV4 does
  // not, so offline tools hash a saved project exactly as it is on disk.
  function upgradeSimConvergence(sc) {
    if (!sc || typeof sc !== 'object' || sc.transientResidualControl !== undefined) return false;
    sc.transientResidualControl = Object.assign({}, DEFAULT_SIM_CONFIG.transientResidualControl);
    if (!(Number(sc.nOuterCorrectors) >= DEFAULT_SIM_CONFIG.nOuterCorrectors)) sc.nOuterCorrectors = DEFAULT_SIM_CONFIG.nOuterCorrectors;
    return true;
  }

  function upgradeFloorPolygon(model) {
    if (!model || !Array.isArray(model.polygon)) return false;
    if (!model.geometry || typeof model.geometry !== 'object') model.geometry = {};
    if (!Array.isArray(model.geometry.polygon)) model.geometry.polygon = model.polygon;
    delete model.polygon;
    return true;
  }

  function normalizeStateV4(rawV4) {
    const st = JSON.parse(JSON.stringify(rawV4));   // deep clone; never mutate caller's object
    const defaults = getInitialStateV4();
    // Fill any missing top-level containers without clobbering present ones (migrated docs
    // already have these; this is a defensive backfill).
    st.geometry    = Object.assign({}, defaults.geometry,    st.geometry);
    st.runDefaults = Object.assign({}, defaults.runDefaults, st.runDefaults);
    st.models      = Object.assign({}, defaults.models,      st.models);
    if (!st.constructions) st.constructions = defaults.constructions;
    // meta / instrumentation were lazily created by the retired state-level alias layer;
    // keep that guarantee so downstream V4-native accessors always find these substructures.
    if (!st.meta || typeof st.meta !== 'object') st.meta = {};
    if (!st.instrumentation || typeof st.instrumentation !== 'object') st.instrumentation = {};
    if (!st.instrumentation.probes || typeof st.instrumentation.probes !== 'object') st.instrumentation.probes = {};
    if (!st.instrumentation.measurements || typeof st.instrumentation.measurements !== 'object') st.instrumentation.measurements = {};
    if (st.schemaVersion == null) st.schemaVersion = 4;

    const CANON = _canonicalKindByBucket();
    for (const bucket of CONSTRUCTION_BUCKETS) {
      const dict = st.constructions[bucket];
      if (!dict) continue;
      for (const uid in dict) {
        const m = dict[uid];
        if (!m || typeof m !== 'object') continue;
        const kind = (m.meta && m.meta.legacyKind) || CANON[bucket] || bucket;
        // Older floor documents kept their footprint on the model. Rendering
        // and export consume the canonical geometry field; preserve the exact
        // vertices when loading so a polygon never becomes its bounding box.
        if (bucket === 'floors') upgradeFloorPolygon(m);
        // kind is the in-memory working discriminator only — keep it NON-enumerable so it
        // never serializes back into the saved .cfdproj (the V4 file derives kind from bucket).
        Object.defineProperty(m, 'kind', { value: kind, enumerable: false, configurable: true, writable: true });
      }
    }
    return st;
  }

  // --- V4-native patch-physics access (Plan 2 T6h, decision B' 2026-06-13) ---
  // In V4, per-patch CFD physics lives on each patch at model.geometry.faces[*].patches[*].physics
  // (the v3 `cfd.patchPhysics[id]` map was distributed onto the patches by the migrator).
  // Orphaned entries (no matching patch) live in model.cooling.unboundPatchPhysics. These helpers
  // are the permanent V4-native replacement for the old `cfd.patchPhysics` map access.

  function _modelPatches(model) {
    const faces = model && model.geometry && model.geometry.faces;
    if (!faces) return [];
    const list = Array.isArray(faces) ? faces : Object.values(faces);
    const out = [];
    for (const f of list) {
      if (f && Array.isArray(f.patches)) for (const p of f.patches) if (p) out.push(p);
    }
    return out;
  }

  function findModelPatch(model, patchId) {
    for (const p of _modelPatches(model)) if (p.id === patchId) return p;
    return null;
  }

  // Reconstruct the {patchId -> physics} map. Bound entries are LIVE references to patch.physics
  // (so callers that mutate fields write through to V4 storage); unbound entries come from
  // cooling.unboundPatchPhysics. Bound overrides unbound on id clash.
  function patchPhysicsMap(model) {
    const map = {};
    const unbound = model && model.cooling && model.cooling.unboundPatchPhysics;
    if (unbound && typeof unbound === 'object') for (const k in unbound) map[k] = unbound[k];
    for (const p of _modelPatches(model)) if (p.id != null && p.physics) map[p.id] = p.physics;
    return map;
  }

  // Live physics object for one patch (or unbound entry); null if none.
  function getPatchPhysics(model, patchId) {
    const p = findModelPatch(model, patchId);
    if (p) return p.physics || null;
    const unbound = model && model.cooling && model.cooling.unboundPatchPhysics;
    return (unbound && unbound[patchId]) || null;
  }

  // Ensure a patch's physics object exists (seeded from `defaults` if absent) and return it.
  // Targets the real geometry patch; if no such patch, falls back to the unbound map.
  function ensurePatchPhysics(model, patchId, defaults) {
    const p = findModelPatch(model, patchId);
    if (p) { if (!p.physics) p.physics = Object.assign({}, defaults || {}); return p.physics; }
    if (!model.cooling) model.cooling = {};
    if (!model.cooling.unboundPatchPhysics) model.cooling.unboundPatchPhysics = {};
    const u = model.cooling.unboundPatchPhysics;
    if (!u[patchId]) u[patchId] = Object.assign({}, defaults || {});
    return u[patchId];
  }

  // Remove a patch's physics. The bound copy lives on the patch (removed with the patch by the
  // caller, if any); this also clears any unbound entry for the id.
  function deletePatchPhysics(model, patchId) {
    const p = findModelPatch(model, patchId);
    if (p && p.physics) delete p.physics;
    const unbound = model && model.cooling && model.cooling.unboundPatchPhysics;
    if (unbound && (patchId in unbound)) delete unbound[patchId];
  }

  return { getInitialStateV4, newDocumentV4, applyNewProjectSizing, bucketForKind, allModels, addConstruction, findConstruction, removeConstruction, clearConstructions, finalizeNewModelV4, normalizeStateV4, upgradeSimConvergence, upgradeFloorPolygon, findModelPatch, patchPhysicsMap, getPatchPhysics, ensurePatchPhysics, deletePatchPhysics };
});
