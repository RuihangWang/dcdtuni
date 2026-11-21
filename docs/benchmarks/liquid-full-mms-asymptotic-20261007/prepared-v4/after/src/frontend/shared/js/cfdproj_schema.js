// cfdproj_schema.js — .cfdproj V4 document schema (single source of truth, spec D7).
// Public API (window.cfdprojSchema / module.exports):
//   SCHEMA_VERSION, BUCKETS, BUCKET_BY_KIND, MESH_KEYS, SIM_KEYS,
//   emptyModels(), emptyConstructions(), emptyDocument(), validateDocument(doc)
// No state access, no migration logic — structure knowledge only.
(function () {
  'use strict';

  const SCHEMA_VERSION = 4;

  // Canonical serialization order of constructions buckets.
  const BUCKETS = [
    'boxes', 'customMeshes', 'surfaces', 'walls', 'partitionWalls', 'baffles',
    'floors', 'racks', 'rows', 'servers', 'acus', 'cdus', 'pipeSegments',
    'pipeJunctions', 'pipes', 'sensors', 'liquidFlowNetworks', 'heatGains',
  ];

  // v3 construction[].kind -> V4 bucket. Legacy dormant kinds land in boxes
  // with meta.legacyKind preserved (see cfdproj_migrate.js).
  const BUCKET_BY_KIND = {
    box: 'boxes', custom: 'customMeshes', wall: 'walls',
    partitionWall: 'partitionWalls', baffle: 'baffles', floor: 'floors',
    rack: 'racks', acu: 'acus', cdu: 'cdus', pipe_segment: 'pipeSegments',
    pipe_junction: 'pipeJunctions',
    pipe: 'pipes',
    heat_source: 'boxes', crh: 'boxes', cold_plate: 'boxes',
  };

  // v3 state.simulation key split: MESH_KEYS -> runDefaults.meshConfig,
  // everything else -> runDefaults.simulationConfig (unknown keys included).
  // test_cfdproj_schema_keysplit.py reconciles this against getInitialState().
  const MESH_KEYS = [
    'meshBaseSize', 'meshDetectBaseSize', 'refinementLevel', 'rackRegionLevel',
    'wallBafflesRefinementLevel', 'floorRefinementLevel', 'floorFeatureLevel',
    'acuRefinementLevel', 'boxRefinementLevel', 'defaultFeatureLevel',
    'acuFeatureLevel', 'boxFeatureLevel', 'baffleFeatureLevel',
    'parallelMesh', 'meshProcess', 'maxGlobalCells',
    'blockMeshBounds', 'blockMeshEditActive', 'blockMeshMoveMode',
    'refinementZones', 'locationInMesh', 'locInMeshVisible',
    'meshMode', 'meshEngine', 'firstCellSize', 'meshTimeLimitSeconds',
    // dcCart sizing keys (2026-09-22): the weld, the first-layer floor and
    // the rack-cell floor are mesh settings, not solver settings.
    'weldTolerance', 'firstLayerMinFraction', 'firstLayerMax', 'firstLayerSealGap', 'rackCellMinSize',
  ];
  const SIM_KEYS = [
    'solverType', 'solverPreset', 'endTime', 'writeInterval', 'endTimeSeconds',
    'deltaT', 'maxDeltaT', 'maxCo', 'adjustTimeStep', 'writeIntervalSeconds',
    'nOuterCorrectors', 'nCorrectors', 'ddtScheme', 'crankNicolsonCoeff',
    'solveProcess', 'parallelSolve', 'gravityX', 'gravityY', 'gravityZ',
    'residualControl', 'transientResidualControl', 'schemes', 'relaxationFactors',
    'nNonOrthogonalCorrectors', 'nonOrthoCorrection', 'doublePrecisionWrite',
    'writePrecision', 'startFrom', 'pressureLinearSolver',
  ];

  function emptyModels() {
    return {
      curveModels: {},
      geometryModels: { boxes: {}, racks: {}, servers: {}, acus: {}, cdus: {}, coldPlates: {}, chips: {}, pipes: {}, surfaces: {} },
      coolingModels: { servers: {}, cdus: {}, coldPlates: {}, heatExchangers: {}, fans: {}, pumps: {}, pipes: {}, valves: {} },
      powerModels: {},
      controlModels: {},
      materialModels: { fluids: {} },
    };
  }

  function emptyConstructions() {
    const c = { raisedFloor: null, falseCeiling: null };
    for (const b of BUCKETS) c[b] = {};
    return c;
  }

  function emptyDocument() {
    return {
      schemaVersion: SCHEMA_VERSION,
      uid: '',
      name: '',
      meta: { projectName: '', exportFileName: '', description: '', inputUnit: 'm', casePath: '' },
      geometry: { height: 3, plane: null, tileSize: 0.6, temperature: 22, bounds: null },
      models: emptyModels(),
      constructions: emptyConstructions(),
      instrumentation: { probes: {}, measurements: {} },
      runDefaults: { meshConfig: {}, simulationConfig: {}, liquid: { enabled: false, solver: {}, inheritResults: false } },
    };
  }

  function validatePressureLinearSolver(value, path, err) {
    if (value === undefined || value === null) return;
    if (typeof value !== 'object' || Array.isArray(value)) {
      err(path, 'must be an object or null'); return;
    }
    const keys = ['preconditioner', 'tolerance', 'relTol', 'maxIter'];
    for (const key of Object.keys(value)) {
      if (!keys.includes(key)) err(path + '.' + key, 'unknown pressure linear solver field');
    }
    for (const key of keys) {
      const field = value[key];
      if (field === undefined || field === null) continue;
      if (key === 'preconditioner') {
        if (field !== 'DIC' && field !== 'GAMG') err(path + '.' + key, 'must be DIC or GAMG');
      } else if (typeof field !== 'number' || !Number.isFinite(field) ||
        (key === 'tolerance' && field <= 0) ||
        (key === 'relTol' && (field < 0 || field >= 1)) ||
        (key === 'maxIter' && (!Number.isInteger(field) || field <= 0))) {
        err(path + '.' + key, 'invalid pressure linear solver numeric value');
      }
    }
  }

  function validateDocument(doc) {
    const errors = [];
    const err = (path, msg) => errors.push({ path, msg });
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      err('$', 'document is not an object');
      return errors;
    }
    // v5 = v4 layout + the `transient` block (time-variable fields live in its
    // tracks; see cfdproj_transient.js). SCHEMA_VERSION stays 4: it is what the
    // v3->v4 migration stamps, and v4->v5 runs after it.
    if (doc.schemaVersion !== SCHEMA_VERSION && doc.schemaVersion !== 5) {
      err('$.schemaVersion', 'expected ' + SCHEMA_VERSION + ' or 5, got ' + doc.schemaVersion);
    }
    if (doc.schemaVersion === 5) {
      const T = (typeof window !== 'undefined' && window.cfdprojTransient) ||
        (typeof require === 'function' ? require('./cfdproj_transient.js') : null);
      if (!doc.transient || typeof doc.transient !== 'object') err('$.transient', 'missing transient block');
      else if (T) for (const e of T.validateTransient(doc.transient)) err('$.' + e.path, e.msg);
    }
    if (typeof doc.uid !== 'string' || !doc.uid) err('$.uid', 'missing uid');
    for (const key of ['meta', 'geometry', 'models', 'constructions', 'instrumentation', 'runDefaults']) {
      if (!doc[key] || typeof doc[key] !== 'object' || Array.isArray(doc[key])) {
        err('$.' + key, 'missing or non-object section');
      }
    }
    const cons = doc.constructions || {};
    for (const bucket of BUCKETS) {
      const dict = cons[bucket];
      if (dict == null) continue;
      if (typeof dict !== 'object' || Array.isArray(dict)) {
        err('$.constructions.' + bucket, 'bucket must be a uid-keyed object');
        continue;
      }
      for (const uid of Object.keys(dict)) {
        const obj = dict[uid];
        const p = '$.constructions.' + bucket + '.' + uid;
        if (!obj || typeof obj !== 'object') { err(p, 'entry must be an object'); continue; }
        if (obj.uid !== uid) err(p, 'uid mismatch: key=' + uid + ' obj.uid=' + obj.uid);
        if (!obj.geometry || typeof obj.geometry !== 'object') err(p + '.geometry', 'missing geometry');
      }
    }
    validatePressureLinearSolver(doc.runDefaults && doc.runDefaults.simulationConfig && doc.runDefaults.simulationConfig.pressureLinearSolver, '$.runDefaults.simulationConfig.pressureLinearSolver', err);
    const experiment = doc.runDefaults && doc.runDefaults.liquid && doc.runDefaults.liquid.experiment;
    if (experiment !== undefined) errors.push(...validateLiquidExperiment(experiment));
    const hybridHeatBudget = doc.runDefaults && doc.runDefaults.hybridHeatBudget;
    if (hybridHeatBudget !== undefined) errors.push(...validateHybridHeatBudget(hybridHeatBudget, doc));
    if (doc.geometryTopology !== undefined) errors.push(...validateGeometryTopology(doc.geometryTopology));
    errors.push(...validateLiquidIsolationConnections(doc));
    return errors;
  }

  // Project-side relation declarations are drafts until source triangles and
  // native cuts are compiled. This validates only the V1 structure and links
  // *within* the declaration; it does not infer physical geometry from UIDs.
  function validateGeometryTopology(value) {
    const errors = [], base = '$.geometryTopology';
    const err = (path, msg) => errors.push({path:base+(path?'.'+path:''),msg});
    const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
    const uid = v => typeof v === 'string' && /^[A-Za-z0-9_./:-]+$/.test(v);
    const fields = (value, path, allowed) => {
      if (!object(value)) { err(path,'expected an object'); return false; }
      Object.keys(value).forEach(k => { if (!allowed.includes(k)) err(path?path+'.'+k:k,'unknown geometry topology field'); });
      allowed.forEach(k => { if (!Object.prototype.hasOwnProperty.call(value,k)) err(path?path+'.'+k:k,'required field'); });
      return true;
    };
    const facePair = (faces, path) => {
      if (!Array.isArray(faces) || faces.length !== 2) { err(path,'expected exactly two source face UIDs'); return; }
      faces.forEach((face,index) => { if (!uid(face)) err(path+'['+index+']','expected a nonempty source face UID'); });
      if (faces[0] === faces[1]) err(path,'source faces must be distinct');
    };
    const positive = (n,path) => {
      if (typeof n !== 'number' || !Number.isFinite(n)) err(path,'must be finite and positive');
      else if (n <= 0) err(path,'must be positive');
    };
    const ref = (value,path,edge) => {
      const keys = edge?['sourceFaceUid','sourceEdgeUid']:['sourceFaceUid'];
      if (!fields(value,path,keys)) return;
      keys.forEach(k => { if (!uid(value[k])) err(path+'.'+k,'expected a nonempty UID'); });
    };
    if (!fields(value,'',['version','mode','coverage','wallPairs','independentPairs','edgeContacts'])) return errors;
    if (value.version !== 1) err('version','expected version 1');
    if (value.mode !== 'strict') err('mode','expected strict');
    if (value.coverage !== 'airInternalSheets') err('coverage','expected airInternalSheets');
    const relationIds = new Map(), wallOwners = new Map(), wallFacePairs = new Set(), gapFacePairs = new Set();
    const validPair = faces => Array.isArray(faces) && faces.length === 2 &&
      faces.every(uid) && faces[0] !== faces[1];
    const pairKey = faces => faces.slice().sort().join('\u0000');
    for (const mapName of ['wallPairs','independentPairs','edgeContacts']) {
      const map = value[mapName];
      if (!object(map)) { err(mapName,'expected a UID-keyed object'); continue; }
      for (const [id, relation] of Object.entries(map)) {
        const path = mapName+'.'+id;
        if (!uid(id)) err(path,'expected a nonempty relation UID');
        if (relationIds.has(id)) err(path,'relation UID occurs in multiple maps (also '+relationIds.get(id)+')');
        else relationIds.set(id,path);
        if (mapName === 'wallPairs') {
          if (!fields(relation,path,['sides','thickness_m','meshReferenceFaceUid','meshTreatment'])) continue;
          facePair(relation.sides,path+'.sides');
          positive(relation.thickness_m,path+'.thickness_m');
          if (!uid(relation.meshReferenceFaceUid)) err(path+'.meshReferenceFaceUid','expected a nonempty source face UID');
          else if (!Array.isArray(relation.sides) || !relation.sides.includes(relation.meshReferenceFaceUid))
            err(path+'.meshReferenceFaceUid','must be one of sides');
          if (relation.meshTreatment !== 'collapsedAirBarrier') err(path+'.meshTreatment','expected collapsedAirBarrier');
          if (validPair(relation.sides)) {
            for (const face of relation.sides) {
              if (wallOwners.has(face))
                err(path,'source face '+face+' belongs to multiple wallPairs (also '+wallOwners.get(face)+')');
              else wallOwners.set(face,path);
            }
            wallFacePairs.add(pairKey(relation.sides));
          }
        } else if (mapName === 'independentPairs') {
          if (!fields(relation,path,['faces','relation','minGap_m'])) continue;
          facePair(relation.faces,path+'.faces');
          positive(relation.minGap_m,path+'.minGap_m');
          if (relation.relation !== 'fluidGap') err(path+'.relation','expected fluidGap');
          if (validPair(relation.faces)) {
            const key = pairKey(relation.faces);
            if (wallFacePairs.has(key)) err(path,'same face pair is already declared in wallPairs');
            if (gapFacePairs.has(key)) err(path,'duplicate independentPairs face pair');
            else gapFacePairs.add(key);
          }
        } else {
          if (!object(relation)) { err(path,'expected an object'); continue; }
          if (relation.kind === 'edgeEdge') {
            fields(relation,path,['kind','a','b']);
            ref(relation.a,path+'.a',true); ref(relation.b,path+'.b',true);
            if (relation.a && relation.b && uid(relation.a.sourceFaceUid) &&
                relation.a.sourceFaceUid === relation.b.sourceFaceUid)
              err(path+'.b.sourceFaceUid','contact source faces must be distinct');
          } else if (relation.kind === 'edgeFace') {
            fields(relation,path,['kind','edge','face']);
            ref(relation.edge,path+'.edge',true); ref(relation.face,path+'.face',false);
            if (relation.edge && relation.face && uid(relation.edge.sourceFaceUid) &&
                relation.edge.sourceFaceUid === relation.face.sourceFaceUid)
              err(path+'.face.sourceFaceUid','contact source faces must be distinct');
          } else err(path+'.kind','expected edgeEdge or edgeFace');
        }
      }
    }
    return errors;
  }

  // Optional, explicitly sourced IT power ledger for a mixed air/liquid case.
  // The existing server cooling.power field is not a total IT power contract.
  function validateHybridHeatBudget(value, doc) {
    const errors = [], base = '$.runDefaults.hybridHeatBudget';
    const err = (path, msg) => errors.push({ path: base + (path ? '.' + path : ''), msg });
    const object = v => v && typeof v === 'object' && !Array.isArray(v);
    if (!object(value)) { err('', 'expected an object'); return errors; }
    const allowed = new Set(['version', 'source', 'source_reference', 'load_mapping', 'air_surface_allocation', 'servers']);
    Object.keys(value).forEach(k => { if (!allowed.has(k)) err(k, 'unknown hybrid heat budget field'); });
    if (value.version !== 1) err('version', 'expected version 1');
    if (!['mock', 'vendor', 'calibrated'].includes(value.source)) err('source', 'expected mock, vendor or calibrated');
    if (typeof value.source_reference !== 'string' || !value.source_reference.trim()) err('source_reference', 'requires an explicit parameter source');
    if (value.load_mapping !== 'shared_heat_multiplier') err('load_mapping', 'expected shared_heat_multiplier');
    const allocation = value.air_surface_allocation;
    if (!object(allocation)) err('air_surface_allocation', 'expected front and rear surface fractions');
    else {
      Object.keys(allocation).forEach(k => {
        if (k !== 'front_fraction' && k !== 'rear_fraction') err('air_surface_allocation.' + k, 'unknown air surface allocation field');
      });
      let finiteNonnegative = true;
      for (const key of ['front_fraction', 'rear_fraction']) {
        if (typeof allocation[key] !== 'number' || !Number.isFinite(allocation[key]) || allocation[key] < 0) {
          err('air_surface_allocation.' + key, 'must be finite and nonnegative');
          finiteNonnegative = false;
        }
      }
      if (finiteNonnegative && Math.abs(allocation.front_fraction + allocation.rear_fraction - 1) > 1e-12) {
        err('air_surface_allocation', 'front and rear fractions must sum to one');
      }
    }
    if (!object(value.servers) || Object.keys(value.servers).length === 0) {
      err('servers', 'expected a nonempty UID-keyed server power object');
      return errors;
    }
    const racks = doc && doc.constructions && doc.constructions.racks;
    for (const [key, entry] of Object.entries(value.servers)) {
      const path = 'servers.' + key;
      const parts = key.split('/');
      if (parts.length !== 2 || !parts[0] || !parts[1] || parts.some(part => part.trim() !== part)) {
        err(path, 'expected rackUid/serverUid using stable construction UIDs');
      } else {
        const [rackUid, serverUid] = parts;
        const rack = object(racks) && Object.prototype.hasOwnProperty.call(racks, rackUid) && racks[rackUid];
        const servers = rack && rack.constructions && rack.constructions.servers;
        const server = object(servers) && Object.prototype.hasOwnProperty.call(servers, serverUid) && servers[serverUid];
        if (!rack || rack.uid !== rackUid || !server || server.uid !== serverUid) {
          err(path, 'must reference an existing rack and its nested server by stable UID');
        }
      }
      if (!object(entry)) { err(path, 'expected an object'); continue; }
      Object.keys(entry).forEach(k => { if (k !== 'total_it_power_W') err(path + '.' + k, 'unknown server power field'); });
      if (typeof entry.total_it_power_W !== 'number' || !Number.isFinite(entry.total_it_power_W) || entry.total_it_power_W <= 0) {
        err(path + '.total_it_power_W', 'must be positive finite watts');
      }
    }
    return errors;
  }

  // Inline isolation fittings belong to an existing rack spoke, never a second
  // parallel connection. The initial lumped representation is co-located with
  // that fitting; no separate pipe length is invented or removed.
  function validateLiquidIsolationConnections(doc) {
    const errors = [], ids = new Map();
    const cons = doc && doc.constructions || {}, pipes = cons.pipeSegments || {};
    for (const [uid, pipe] of Object.entries(pipes)) {
      const routeMode=pipe && pipe.geometry && pipe.geometry.routeMode;
      if (routeMode !== undefined && !['smoothed','polyline'].includes(routeMode))
        errors.push({path:'$.constructions.pipeSegments.'+uid+'.geometry.routeMode',msg:'must be smoothed or polyline'});
      const valves = pipe && pipe.cooling && pipe.cooling.valves;
      if (valves == null) continue;
      const base = '$.constructions.pipeSegments.' + uid + '.cooling.valves';
      if (!Array.isArray(valves)) { errors.push({path:base,msg:'must be an array'}); continue; }
      valves.forEach((v, index) => {
        const path = base + '[' + index + ']';
        const err = (field,msg) => errors.push({path:path+(field?'.'+field:''),msg});
        if (!v || typeof v !== 'object' || Array.isArray(v)) { err('','must be an object'); return; }
        if (typeof v.id !== 'string' || !v.id) err('id','must be a nonempty stable UID');
        else if (ids.has(v.id)) err('id','duplicate valve UID, also used at '+ids.get(v.id));
        else ids.set(v.id,path);
        const point = p => p && ['x','y','z'].every(k => typeof p[k] === 'number' && Number.isFinite(p[k]));
        if (v.branchDiameterM !== undefined && !(typeof v.branchDiameterM === 'number' && Number.isFinite(v.branchDiameterM) && v.branchDiameterM > 0))
          err('branchDiameterM','must be finite and positive');
        if (v.branchWaypoints !== undefined && (!Array.isArray(v.branchWaypoints) || v.branchWaypoints.length < 2 || !v.branchWaypoints.every(point)))
          err('branchWaypoints','must contain at least two finite metre-coordinate points');
        if (v.type === 'flow_control' && v.seriesWithValveId !== undefined) {
          const target = valves.find(x => x && x.id === v.seriesWithValveId);
          if (!target || target === v || target.type !== 'tee_junction' || !target.connectedRackId || !target.connectedManifoldPortId)
            err('seriesWithValveId','must reference one connected tee on this same pipe');
          if (v.connectedRackId || v.connectedManifoldPortId) err('seriesWithValveId','series valve must not create a second parallel rack connection');
          if (!point(v.position)) err('position','must be finite; compilation verifies it lies inside the branch');
          const opening = v.opening === undefined ? 1 : v.opening;
          if (!(typeof opening === 'number' && Number.isFinite(opening) && opening > 0 && opening <= 1))
            err('opening','must be positive and <=1 in the fixed active set');
          return;
        }
        if (v.type !== 'isolation') return;
        const allowed = new Set(['id','type','seriesWithValveId','opening','position','name','side','meta']);
        Object.keys(v).forEach(k=>{if(!allowed.has(k))err(k,'unknown inline isolation field; a second rack connection is not allowed');});
        if (typeof v.opening !== 'number' || !Number.isFinite(v.opening) || v.opening <= 0 || v.opening > 1) err('opening','must be positive and <=1; active-set switching is not supported');
        const target = valves.find(x=>x && x.id === v.seriesWithValveId);
        if (typeof v.seriesWithValveId !== 'string' || !target || target === v || !['tee_junction','flow_control'].includes(target.type)) {
          err('seriesWithValveId','must reference a tee or energy/control fitting on this pipe'); return;
        }
        const rack = (cons.racks || {})[target.connectedRackId];
        if (!rack || typeof target.connectedManifoldPortId !== 'string' || !target.connectedManifoldPortId.startsWith(target.connectedRackId+'.') || !/(supply|return)/.test(target.connectedManifoldPortId)) err('seriesWithValveId','target must connect this pipe to a valid rack manifold');
        for (const axis of ['x','y','z']) {
          const a = v.position && v.position[axis], b = target.position && target.position[axis];
          if (!Number.isFinite(a) || !Number.isFinite(b) || Math.abs(a-b)>1e-8) err('position.'+axis,'must coincide with the referenced fitting in the current lumped model');
        }
      });
    }
    for (const [uid,junction] of Object.entries(cons.pipeJunctions || {})) {
      const j=junction && junction.cooling || {};
      if (j.fittingDiameterM === undefined) continue;
      const path='$.constructions.pipeJunctions.'+uid+'.cooling';
      const err=msg=>errors.push({path,msg});
      if (!(typeof j.fittingDiameterM === 'number' && Number.isFinite(j.fittingDiameterM) && j.fittingDiameterM > 0))
        err('physical fitting diameter must be finite and positive');
      if (!Array.isArray(j.ports) || j.ports.length < 2) err('physical fitting needs at least two declared ports');
      else for (const port of j.ports) {
        const point=p=>p && ['x','y','z'].every(k=>typeof p[k]==='number' && Number.isFinite(p[k]));
        if (!port || !point(port.offset) || !point(port.direction) ||
            Math.hypot(port.offset.x,port.offset.y,port.offset.z)===0 ||
            Math.hypot(port.direction.x,port.direction.y,port.direction.z)===0)
          err('physical fitting needs finite nonzero port offsets and directions');
      }
    }
    // Optional physical CDU frames preserve old projects when absent.
    for (const [uid,cdu] of Object.entries(cons.cdus || {})) {
      const ports = cdu && cdu.cooling && cdu.cooling.ports;
      if (ports === undefined) continue;
      const path = '$.constructions.cdus.'+uid+'.cooling.ports';
      const err = msg => errors.push({path,msg});
      const point = p => p && ['x','y','z'].every(k => typeof p[k] === 'number' && Number.isFinite(p[k]));
      if (!Array.isArray(ports) || ports.length !== 2 || new Set(ports.map(p=>p && p.id)).size !== 2 ||
          !ports.some(p=>p && p.id === 'supply') || !ports.some(p=>p && p.id === 'return')) {
        err('must declare exactly one supply and one return physical port'); continue;
      }
      for (const p of ports) {
        if (!point(p.position) || !point(p.direction) || Math.hypot(p.direction.x,p.direction.y,p.direction.z) === 0)
          err('physical positions and nonzero directions must be finite');
        if (!(typeof p.diameterM === 'number' && Number.isFinite(p.diameterM) && p.diameterM > 0))
          err('physical port diameter must be finite and positive');
      }
      if (ports.every(p=>point(p.position) && Number.isFinite(p.diameterM)) &&
          Math.hypot(...['x','y','z'].map(k=>ports[0].position[k]-ports[1].position[k])) < (ports[0].diameterM+ports[1].diameterM)/2)
        err('physical supply/return port envelopes intersect');
    }
    return errors;
  }

  // Saved scenarios are part of the document, not transient React state.
  // Keep this validator shared by load/save and compilation in both editors.
  function validateLiquidExperiment(value) {
    const errors = [], base = '$.runDefaults.liquid.experiment';
    const err = (key, msg) => errors.push({path:base + (key ? '.' + key : ''),msg});
    const object = v => v && typeof v === 'object' && !Array.isArray(v);
    if (!object(value)) { err('', 'expected an object'); return errors; }
    const allowed = new Set(['engine','duration_s','dt_s','settings','parameterPack','dae']);
    Object.keys(value).forEach(k => { if (!allowed.has(k)) err(k, 'unknown experiment field'); });
    for (const key of ['duration_s','dt_s']) {
      if (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] <= 0) err(key, 'must be positive and finite');
    }
    const intervals = value.duration_s / value.dt_s;
    if (Number.isFinite(intervals) && Math.abs(intervals - Math.round(intervals)) > 1e-8) err('duration_s', 'must lie on the time-step grid');
    if (value.engine !== undefined && value.engine !== 'newton-dae') err('engine', 'unknown solver intent');
    const s = value.settings;
    if (!object(s)) { err('settings', 'expected controller and load settings'); return errors; }
    const keys = new Set(['mode','pumpId','measurementId','setpoint','kp','ki','bias','loadStep','stepTime','loadFactor','supplySchedule']);
    Object.keys(s).forEach(k => { if (!keys.has(k)) err('settings.'+k, 'unknown setting'); });
    if (!['manual','temperature','pressure'].includes(s.mode)) err('settings.mode', 'unknown control mode');
    for (const key of ['pumpId','measurementId']) if (typeof s[key] !== 'string') err('settings.'+key, 'must be a string UID');
    for (const key of ['setpoint','kp','ki','bias','stepTime','loadFactor']) {
      if (typeof s[key] !== 'number' || !Number.isFinite(s[key])) err('settings.'+key, 'must be finite');
    }
    if (s.kp < 0 || s.ki < 0) err('settings.kp','PI gains must be nonnegative');
    if (s.bias < .2 || s.bias > 1) err('settings.bias','must be between 0.2 and 1');
    if (typeof s.loadStep !== 'boolean') err('settings.loadStep','must be boolean');
    if (s.loadFactor < 0) err('settings.loadFactor','must be nonnegative');
    if (s.loadStep && (!(s.stepTime > 0) || s.stepTime > value.duration_s || Math.abs(s.stepTime/value.dt_s-Math.round(s.stepTime/value.dt_s))>1e-8)) err('settings.stepTime','must lie on the simulation grid within its duration');
    if (s.supplySchedule !== undefined) {
      // Creation-time intent for the facility supply temperature; the React
      // compiler expands it into primary_circuits[].inlet_K (v3-schema-native).
      const ss = s.supplySchedule;
      const pos = (v, name) => { if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) err(name, 'must be a positive finite number'); };
      if (!object(ss)) err('settings.supplySchedule', 'expected an object');
      else {
        Object.keys(ss).forEach(k => { if (!['mode','from','to','stepTime','rampTime','mean','amplitude','period'].includes(k)) err('settings.supplySchedule.'+k, 'unknown supply-schedule field'); });
        if (!['fixed','step','ramp','sinusoid'].includes(ss.mode)) err('settings.supplySchedule.mode', 'unknown supply-schedule mode');
        else if (ss.mode === 'step' || ss.mode === 'ramp') {
          pos(ss.from, 'settings.supplySchedule.from'); pos(ss.to, 'settings.supplySchedule.to');
          pos(ss.stepTime, 'settings.supplySchedule.stepTime');
          if (typeof ss.stepTime === 'number' && Number.isFinite(ss.stepTime) &&
              (ss.stepTime > value.duration_s || Math.abs(ss.stepTime/value.dt_s-Math.round(ss.stepTime/value.dt_s))>1e-8))
            err('settings.supplySchedule.stepTime', 'must lie on the simulation grid within its duration');
          if (ss.mode === 'ramp') pos(ss.rampTime, 'settings.supplySchedule.rampTime');
        } else if (ss.mode === 'sinusoid') {
          pos(ss.mean, 'settings.supplySchedule.mean'); pos(ss.period, 'settings.supplySchedule.period');
          if (typeof ss.amplitude !== 'number' || !Number.isFinite(ss.amplitude) || ss.amplitude < 0) err('settings.supplySchedule.amplitude', 'must be finite and nonnegative');
          if (typeof ss.mean === 'number' && typeof ss.amplitude === 'number' && ss.mean - ss.amplitude <= 0) err('settings.supplySchedule', 'sinusoid must stay positive (Kelvin) at every sample');
        }
      }
    }
    if (s.mode === 'temperature' && s.setpoint <= -273.15) err('settings.setpoint','must exceed absolute zero');
    if (s.mode === 'pressure' && s.setpoint <= 0) err('settings.setpoint','pressure difference must be positive');
    if (value.parameterPack !== undefined && (!object(value.parameterPack) || !object(value.parameterPack.profiles) || typeof value.parameterPack.id !== 'string' || typeof value.parameterPack.revision !== 'string')) err('parameterPack','expected a versioned equipment pack');
    if (value.dae !== undefined) {
      const d = value.dae;
      const strictObject = (input, path, fields, optional = []) => {
        if (!object(input)) { err(path, 'expected an object'); return false; }
        Object.keys(input).forEach(k => { if (!fields.includes(k) && !optional.includes(k)) err(path+'.'+k, 'unknown DAE field'); });
        fields.forEach(k => { if (input[k] === undefined) err(path+'.'+k, 'required DAE field'); });
        return true;
      };
      const numeric = (input, path, positive) => {
        if (typeof input !== 'number' || !Number.isFinite(input) || (positive ? input <= 0 : input < 0)) err(path, positive ? 'must be positive and finite' : 'must be nonnegative and finite');
      };
      if (s.mode !== 'manual') err('settings.mode','DAE requires manual schedules; PID control is not supported');
      if (!object(value.parameterPack)) err('parameterPack','DAE requires an explicitly saved equipment pack');
      if (strictObject(d,'dae',['equation_system','analysis','newton','primary_circuits'],['execution','manufactured_benchmark'])) {
        if (d.manufactured_benchmark !== undefined) {
          if (!object(d.manufactured_benchmark) || d.manufactured_benchmark.format !== 'dcdtuni-manufactured-benchmark' || d.manufactured_benchmark.version !== 1) err('dae.manufactured_benchmark','expected explicit versioned research benchmark');
          if (!object(d.analysis) || d.analysis.mode !== 'transient' || d.analysis.initialization !== 'consistent') err('dae.manufactured_benchmark','requires transient/consistent analysis');
        }
        if (d.equation_system !== 'incompressible_thermal_dae') err('dae.equation_system','unknown DAE equation system');
        if (d.execution !== undefined && strictObject(d.execution,'dae.execution',['backend','device','dtype','max_threads'])) {
          if (d.execution.backend !== 'pytorch') err('dae.execution.backend','expected pytorch');
          if (typeof d.execution.device !== 'string' || !/^(cpu|cuda(?::[0-9]+)?)$/.test(d.execution.device)) err('dae.execution.device','expected cpu or cuda device');
          if (!['float32','float64'].includes(d.execution.dtype)) err('dae.execution.dtype','expected float32 or float64');
          if (!Number.isSafeInteger(d.execution.max_threads) || d.execution.max_threads < 1 || d.execution.max_threads > 256) err('dae.execution.max_threads','expected 1..256 threads');
        }
        if (strictObject(d.analysis,'dae.analysis',['mode','initialization'])) {
          if (!['steady','transient'].includes(d.analysis.mode)) err('dae.analysis.mode','expected steady or transient');
          if (!['steady','consistent'].includes(d.analysis.initialization)) err('dae.analysis.initialization','expected steady or consistent');
          if (d.analysis.mode === 'steady') {
            if (d.analysis.initialization !== 'steady') err('dae.analysis.initialization','steady mode requires steady initialization');
            if (s.loadStep) err('dae.analysis.mode','steady mode cannot discard a configured load event');
          }
        }
        if (strictObject(d.newton,'dae.newton',['max_iterations','atol','rtol','max_backtracks'])) {
          ['max_iterations','max_backtracks'].forEach(k => {
            if (!Number.isSafeInteger(d.newton[k]) || d.newton[k] < 1) err('dae.newton.'+k,'must be a positive integer');
          });
          numeric(d.newton.atol,'dae.newton.atol',true);
          numeric(d.newton.rtol,'dae.newton.rtol',false);
        }
        if (!Array.isArray(d.primary_circuits)) err('dae.primary_circuits','expected one binding per HX');
        else {
          const samples = d.analysis && d.analysis.mode === 'steady' ? 1 : Math.round(intervals)+1;
          const ids = new Set();
          d.primary_circuits.forEach((entry,i) => {
            const path = 'dae.primary_circuits.'+i;
            const fields = ['edge_id','flow_kg_s','inlet_K','cp_J_kgK','rho_kg_m3','primary_volume_m3','secondary_volume_m3','source','source_reference'];
            if (!strictObject(entry,path,fields)) return;
            if (typeof entry.edge_id !== 'string' || !entry.edge_id.trim()) err(path+'.edge_id','requires a stable HX edge UID');
            else if (ids.has(entry.edge_id)) err(path+'.edge_id','duplicate HX binding');
            ids.add(entry.edge_id);
            for (const key of ['flow_kg_s','inlet_K']) {
              const series = entry[key];
              if (Array.isArray(series)) {
                if (series.length !== samples) err(path+'.'+key,'series must contain exactly N_t samples');
                series.forEach((item,j) => numeric(item,path+'.'+key+'.'+j,key === 'inlet_K'));
              } else numeric(series,path+'.'+key,key === 'inlet_K');
            }
            ['cp_J_kgK','rho_kg_m3','primary_volume_m3','secondary_volume_m3'].forEach(k => numeric(entry[k],path+'.'+k,true));
            if (!['mock','vendor','calibrated'].includes(entry.source)) err(path+'.source','expected mock, vendor or calibrated');
            if (typeof entry.source_reference !== 'string' || !entry.source_reference.trim()) err(path+'.source_reference','requires an explicit parameter source');
          });
        }
      }
      const profiles = value.parameterPack && value.parameterPack.profiles;
      if (object(profiles)) Object.entries(profiles).forEach(([id,profile]) => {
        if (object(profile) && profile.type === 'heat_exchanger' && object(profile.params) && 'T_secondary_K' in profile.params)
          err('parameterPack.profiles.'+id+'.params.T_secondary_K','DAE uses explicit primary_circuits, not an infinite reservoir');
      });
    }
    return errors;
  }

  const api = {
    SCHEMA_VERSION, BUCKETS, BUCKET_BY_KIND, MESH_KEYS, SIM_KEYS,
    emptyModels, emptyConstructions, emptyDocument, validateDocument, validateGeometryTopology, validateLiquidExperiment, validateLiquidIsolationConnections, validateHybridHeatBudget,
  };
  if (typeof window !== 'undefined') window.cfdprojSchema = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
