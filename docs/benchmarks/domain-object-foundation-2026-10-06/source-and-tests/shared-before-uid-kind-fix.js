(function () {
  'use strict';
  const SCHEMA_VERSION = 3;

  // Plain JSON merges must preserve an own "__proto__" extension as data.
  // Define each own field in source order, retaining overwrite/null-skip semantics.
  function _setOwn(target, key, value) {
    Object.defineProperty(target, key, {
      value, enumerable: true, configurable: true, writable: true,
    });
  }

  function _assignRecord(target, ...sources) {
    for (const source of sources) {
      if (source == null) continue;
      for (const key of Object.keys(source)) _setOwn(target, key, source[key]);
    }
    return target;
  }

  // keys consumed explicitly below; everything else is carried forward verbatim
  const HANDLED = new Set([
    'schemaVersion', 'projectName', 'exportFileName', 'inputUnit', 'bounds', 'roomPolygon',
    'models', 'sensorPoints', 'cfdRoom', 'dctwinConfig', 'displayMode', 'view',
    'snapFace', 'snapEdge', 'snapGrid', 'treeCollapsed', 'selection', 'groups',
    'previewVisibility', 'previewRegionVisibility', 'previewRegionReverse',
    'rackDetailTarget', 'rackDetailReturnMode', 'rackDetailShowContext',
    '_selectedCategories', '_anchorCategory', '_lastClickedCategory', 'liquidCooling',
  ]);

  function migrateCfdprojV2toV3(raw) {
    if (!raw || typeof raw !== 'object') return raw;
    if (Number(raw.schemaVersion || 0) >= SCHEMA_VERSION) return raw;

    const cfdRoom = (raw.cfdRoom && typeof raw.cfdRoom === 'object') ? { ...raw.cfdRoom } : {};
    const roomHeight = (cfdRoom.height != null) ? cfdRoom.height : 3.6;
    const roomTemp = (cfdRoom.roomTemperature != null) ? cfdRoom.roomTemperature : 22;
    delete cfdRoom.height;
    delete cfdRoom.roomTemperature;

    const dct = (raw.dctwinConfig && typeof raw.dctwinConfig === 'object') ? { ...raw.dctwinConfig } : {};
    const casePath = dct.casePath || '';
    delete dct.casePath;

    const v3 = {
      schemaVersion: SCHEMA_VERSION,
      meta: {
        projectName: typeof raw.projectName === 'string' ? raw.projectName : '',
        exportFileName: typeof raw.exportFileName === 'string' ? raw.exportFileName : 'default',
        inputUnit: raw.inputUnit || 'mm',
        description: '',
        room: {
          bounds: raw.bounds || {},
          // roomPolygon is an object {vertices:[[x,z]...], height} (NOT an array);
          // pass it through verbatim, default null (matches v2 `raw.roomPolygon || null`).
          polygon: (raw.roomPolygon != null) ? raw.roomPolygon : null,
          height: roomHeight,
          temperature: roomTemp,
        },
        case: { path: casePath },
      },
      model: { racks: {}, acus: {} },
      construction: Array.isArray(raw.models)
        ? raw.models.map(m => ({ ...m, fromModel: (m && m.fromModel != null) ? m.fromModel : null }))
        : [],
      probeData: Array.isArray(raw.sensorPoints) ? raw.sensorPoints : [],
      sensorData: [],
      simulation: cfdRoom,
      liquidCooling: raw.liquidCooling || {},
      ui: {
        displayMode: raw.displayMode || 'air',
        view: raw.view || {},
        snapFace: raw.snapFace !== false,
        snapEdge: raw.snapEdge !== false,
        snapGrid: !!raw.snapGrid,
        treeCollapsed: raw.treeCollapsed || {},
        selection: raw.selection || {},
        groups: Array.isArray(raw.groups) ? raw.groups : [],
        previewVisibility: raw.previewVisibility || {},
        previewRegionVisibility: raw.previewRegionVisibility || {},
        previewRegionReverse: raw.previewRegionReverse || {},
        rackDetail: {
          target: raw.rackDetailTarget != null ? raw.rackDetailTarget : null,
          returnMode: raw.rackDetailReturnMode || '3d',
          showContext: !!raw.rackDetailShowContext,
        },
        _selectedCategories: Array.isArray(raw._selectedCategories) ? raw._selectedCategories : [],
        _anchorCategory: raw._anchorCategory != null ? raw._anchorCategory : null,
        _lastClickedCategory: raw._lastClickedCategory != null ? raw._lastClickedCategory : null,
        session: dct,
      },
    };
    // forward-compat: carry any unrecognized top-level keys so on-disk batch
    // conversion never silently drops data.
    for (const k of Object.keys(raw)) {
      if (!HANDLED.has(k) && !Object.prototype.hasOwnProperty.call(v3, k)) _setOwn(v3, k, raw[k]);
    }
    return v3;
  }

  // ---- v3 -> v4 (spec 2026-06-12-cfdproj-v4-cfdrun-v2-design.md §5) ----

  function _schema() {
    if (typeof window !== 'undefined' && window.cfdprojSchema) return window.cfdprojSchema;
    if (typeof require !== 'undefined') return require('./cfdproj_schema.js');
    throw new Error('cfdproj_schema.js not available');
  }

  function _sanitizeUid(s) { return String(s || 'untitled').replace(/[^A-Za-z0-9_]/g, '_'); }

  function _keyByName(arr) {
    const out = {};
    for (const item of Array.isArray(arr) ? arr : []) {
      if (item && item.name != null) out[item.name] = item;
    }
    return out;
  }

  function _splitSimulation(sim, S) {
    const meshConfig = {}, simulationConfig = {};
    for (const [k, v] of Object.entries(sim || {})) {
      (S.MESH_KEYS.includes(k) ? meshConfig : simulationConfig)[k] = v;
    }
    return { meshConfig, simulationConfig };
  }

  // Real cfdproj stores construction `faces` as an object keyed by face id
  // ({front:{...}, back:{...}}); some fixtures use an array. Normalize to a flat
  // list of face entries (each retaining its live `patches` array reference) so
  // callers can iterate either shape. Non-face-object/array input yields [].
  function _facesList(faces) {
    if (Array.isArray(faces)) return faces;
    if (faces && typeof faces === 'object') return Object.values(faces);
    return [];
  }

  function _distributePatchPhysics(faces, patchPhysics) {
    const left = _assignRecord({}, patchPhysics || {});
    for (const face of _facesList(faces)) {
      for (const patch of Array.isArray(face.patches) ? face.patches : []) {
        if (patch && patch.id != null && left[patch.id] !== undefined) {
          const phys = left[patch.id];
          delete left[patch.id];
          const { perforated, ...rest } = phys || {};
          patch.physics = rest;
          if (perforated) patch.perforation = perforated;
        }
      }
    }
    return left;
  }

  function _commonV4(m) {
    const {
      id, kind, name, enabled, opacity, showAnchorMarker,
      anchor, base, transform, faces, cutouts, cfd,
      ...rest
    } = m;
    const geometry = { location: anchor ? { x: anchor.x, y: anchor.y, z: anchor.z } : { x: 0, y: 0, z: 0 } };
    if (base) geometry.size = { x: base.W, y: base.H, z: base.D };
    if (transform && transform.quaternion) geometry.quaternion = transform.quaternion;
    if (transform && transform.scale) geometry.scale = transform.scale;
    if (faces !== undefined) geometry.faces = faces;
    if (cutouts !== undefined) geometry.cutouts = cutouts;
    const meta = {
      name: name != null ? name : id,
      enabled: enabled !== false,
      description: '', tags: [],
      display: { opacity: opacity, showAnchorMarker: showAnchorMarker },
    };
    return { out: { uid: id, geometry, cooling: {}, meta }, cfd: cfd || {}, rest };
  }

  function _migrateBoxLike(m, legacyKind) {
    const { out, cfd, rest } = _commonV4(m);
    if (legacyKind) out.meta.legacyKind = legacyKind;
    const { heatSource, crh, coldPlate, ...rest2 } = rest;
    if (heatSource) {
      out.cooling.boundaryMode = heatSource.heatMode === 'temperature' ? 'fixedTemperature' : 'power';
      if (heatSource.totalPower != null) out.cooling.totalPowerW = heatSource.totalPower;
      if (heatSource.fixedTemperature != null) out.cooling.fixedTemperatureC = heatSource.fixedTemperature;
      const { heatMode, totalPower, fixedTemperature, ...hsRest } = heatSource;
      _assignRecord(out.cooling, hsRest);
    }
    if (crh) out.crh = crh;
    if (coldPlate) out.coldPlate = coldPlate;
    const { role, patchPhysics, ...cfdRest } = cfd;
    const leftover = _distributePatchPhysics(out.geometry.faces, patchPhysics);
    if (Object.keys(leftover).length) out.cooling.unboundPatchPhysics = leftover;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest2);
  }

  // Real v3 floors store defaultBC as an object {type:'wall'} (case files are
  // ground truth); some fixtures use the bare string 'wall'. Normalize to the
  // string boundary type either way.
  function _normalizeDefaultBC(defaultBC) {
    if (defaultBC && typeof defaultBC === 'object' && !Array.isArray(defaultBC)) {
      return defaultBC.type || 'wall';
    }
    return defaultBC || 'wall';
  }

  function _migrateFloor(m) {
    const { out, cfd, rest } = _commonV4(m);
    if (Array.isArray(rest.polygon)) {
      out.geometry.polygon = rest.polygon;
      delete rest.polygon;
    }
    const { role, roomBoundaryType, defaultBC, defaultBCPerforated, defaultBCSeedConfig,
            defaultBCSeedConfigs, openings, patchPhysics, ...cfdRest } = cfd;
    out.cooling.roomBoundaryLocation = roomBoundaryType || 'none';
    out.cooling.defaultBoundaryType = _normalizeDefaultBC(defaultBC);
    if (defaultBCPerforated) out.cooling.defaultPerforation = defaultBCPerforated;
    if (defaultBCSeedConfig) out.cooling.defaultSeedConfig = defaultBCSeedConfig;
    if (defaultBCSeedConfigs) out.cooling.defaultSeedConfigs = defaultBCSeedConfigs;
    out.geometry.openings = (Array.isArray(openings) ? openings : []).map(op => {
      const { perforated, ...opRest } = op || {};
      return perforated ? _assignRecord(opRest, { perforation: perforated }) : opRest;
    });
    const leftover = _distributePatchPhysics(out.geometry.faces, patchPhysics);
    if (Object.keys(leftover).length) out.cooling.unboundPatchPhysics = leftover;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest);
  }

  function _migratePlanar(m) {  // wall, baffle, partitionWall
    const { out, cfd, rest } = _commonV4(m);
    const { role, baffleType, perforated, patchPhysics, seedConfig, openings, holes, leakage, ...cfdRest } = cfd;
    if (baffleType) out.cooling.type = baffleType;
    if (perforated) out.cooling.perforation = perforated;
    if (seedConfig) out.cooling.seedConfig = seedConfig;
    if (leakage) out.cooling.leakage = leakage;
    if (openings) out.geometry.openings = openings;
    if (holes) out.geometry.openings = holes;
    const leftover = _distributePatchPhysics(out.geometry.faces, patchPhysics);
    if (Object.keys(leftover).length) out.cooling.unboundPatchPhysics = leftover;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest);
  }

  // Convert a single v3 construction model `m` to its V4 construction object.
  // This is the per-model body of migrateCfdprojV3toV4's construction loop, factored
  // out (DRY) so the model creation layer (factories) can emit native V4 via the same
  // path the load migrator uses. Output MUST stay byte-identical to the inline switch.
  function migrateModelV3toV4(m) {
    const S = _schema();
    const legacy = (S.BUCKET_BY_KIND[m.kind] === 'boxes' && m.kind !== 'box') ? m.kind : undefined;
    let obj;
    switch (m.kind) {
      case 'rack': obj = _migrateRack(m); break;
      case 'acu': obj = _migrateAcu(m); break;
      case 'cdu': obj = _migrateCdu(m); break;
      case 'pipe_segment': obj = _migratePipe(m); break;
      case 'pipe_junction': obj = _migrateJunction(m); break;
      case 'pipe': obj = _migrateAirPipe(m); break;
      case 'floor': obj = _migrateFloor(m); break;
      case 'wall': case 'baffle': case 'partitionWall': obj = _migratePlanar(m); break;
      case 'custom': obj = _migrateCustom(m); break;
      default: obj = _migrateBoxLike(m, legacy); break;
    }
    return obj;
  }

  function migrateCfdprojV3toV4(raw) {
    if (!raw || typeof raw !== 'object') return raw;
    if ((raw.schemaVersion || 0) >= 4) return raw;
    const S = _schema();
    const meta3 = raw.meta || {};
    const room = meta3.room || {};
    const lc = raw.liquidCooling || {};
    const specs = lc.specifications || {};

    const doc = S.emptyDocument();
    doc.uid = raw.uid || ('proj_' + _sanitizeUid(meta3.projectName));
    doc.name = meta3.projectName || '';
    doc.meta = {
      projectName: meta3.projectName || '',
      exportFileName: meta3.exportFileName || '',
      description: meta3.description || '',
      inputUnit: meta3.inputUnit || 'm',
      casePath: (meta3.case && meta3.case.path) || '',
    };
    doc.geometry = {
      height: room.height != null ? room.height : 3,
      plane: room.polygon ? room.polygon.vertices : null,
      tileSize: 0.6,
      temperature: room.temperature != null ? room.temperature : 22,
      bounds: room.bounds != null ? room.bounds : null,
    };
    if (room.polygon && room.polygon.height != null && room.polygon.height !== room.height) {
      doc.geometry.planeHeight = room.polygon.height;
    }

    const model3 = raw.model || {};
    doc.models.geometryModels.racks = model3.racks || {};
    doc.models.geometryModels.acus = model3.acus || {};
    doc.models.coolingModels.coldPlates = _keyByName(specs.coldPlateSpec);
    doc.models.coolingModels.pipes = _keyByName(specs.pipeSpec);
    doc.models.coolingModels.pumps = _keyByName(specs.pumpSpec);
    doc.models.coolingModels.heatExchangers = _keyByName(specs.hxSpec);
    doc.models.coolingModels.valves = _keyByName(specs.valveSpec);
    if (lc.fluid && lc.fluid.type) doc.models.materialModels.fluids[lc.fluid.type] = lc.fluid;

    for (const m of Array.isArray(raw.construction) ? raw.construction : []) {
      const obj = migrateModelV3toV4(m);
      const bucket = S.BUCKET_BY_KIND[m.kind] || 'boxes';
      doc.constructions[bucket][obj.uid] = obj;
    }

    for (const p of Array.isArray(raw.probeData) ? raw.probeData : []) {
      const { id, name, x, y, z, enabled, ...pRest } = p;
      // Skip id-less probes (mirrors the sensorData guard below and the Python
      // migrator). An undefined id would otherwise become the literal 'undefined'
      // object key here while Python produces an unserializable sentinel key.
      if (id == null) continue;
      doc.instrumentation.probes[id] = _assignRecord(
        { uid: id, geometry: { location: { x, y, z } }, meta: { name: name, enabled: enabled !== false } }, pRest);
    }
    for (const s of Array.isArray(raw.sensorData) ? raw.sensorData : []) {
      if (s && s.id != null) doc.instrumentation.measurements[s.id] = s;
    }

    const split = _splitSimulation(raw.simulation, S);
    doc.runDefaults.meshConfig = split.meshConfig;
    doc.runDefaults.simulationConfig = split.simulationConfig;
    doc.runDefaults.liquid = {
      enabled: lc.enabled === true,
      solver: lc.solver || {},
      inheritResults: lc.inheritResults === true,
    };
    // Air projects often carry a stale `piping.autoGenerated:true` default while
    // `liquidCooling.enabled` is false. Emit a liquidFlowNetwork only when liquid
    // cooling is actually enabled, so air cases stay free of phantom networks.
    if (lc.enabled === true && lc.piping &&
        (lc.piping.autoGenerated !== undefined || lc.piping.mainPipeSpec || lc.piping.branchPipeSpec)) {
      const { chains, ...pipingRest } = lc.piping;
      doc.constructions.liquidFlowNetworks.net_main = _assignRecord(
        { uid: 'net_main', geometry: {}, meta: { name: 'main', enabled: true } }, pipingRest);
    }

    const known = new Set(['schemaVersion', 'meta', 'model', 'construction', 'probeData',
                           'sensorData', 'simulation', 'liquidCooling', 'ui', 'uid']);
    for (const [k, v] of Object.entries(raw)) if (!known.has(k)) _setOwn(doc, k, v);
    return doc;
  }

  function migrateCfdprojToV4(raw) {
    return migrateCfdprojV3toV4(migrateCfdprojV2toV3(raw));
  }

  function _migrateRack(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { rack, ...rest2 } = rest;
    const r = rack || {};
    const { unitCount, unitHeight, slotWidthRatio, coolingType, liquidManifold,
            equipments, partitions, inletDoor, outletDoor, blankingPanelType, ...rackRest } = r;
    if (unitCount != null) out.geometry.slot = unitCount;
    if (unitHeight != null) out.geometry.slotHeight = unitHeight;
    if (slotWidthRatio != null) out.geometry.slotWidthRatio = slotWidthRatio;
    out.geometry.faces = Array.isArray(out.geometry.faces) ? out.geometry.faces : [];
    for (const [uid, door] of [['inletDoor', inletDoor], ['outletDoor', outletDoor]]) {
      if (!door) continue;
      const { perforated, ...doorRest } = door;
      out.geometry.faces.push(_assignRecord({ uid, role: 'door' },
        doorRest, perforated ? { perforation: perforated } : {}));
    }
    const { role, leakage, servers: physicsList, patchPhysics, ...cfdRest } = cfd;
    out.cooling.type = coolingType || 'air';
    if (liquidManifold) out.cooling.liquidManifold = liquidManifold;
    if (blankingPanelType !== undefined) out.cooling.blankingPanelType = blankingPanelType;
    out.cooling.leakageEnabled = !!(leakage && leakage.enabled);
    out.cooling.defaultLeakageSigma = (leakage && leakage.sigma != null) ? leakage.sigma : 0.05;
    if (leakage) {
      const { enabled, sigma, ...lkRest } = leakage;
      if (Object.keys(lkRest).length) out.cooling.leakageExtra = lkRest;
    }
    const physById = {};
    for (const p of Array.isArray(physicsList) ? physicsList : []) {
      if (p && p.equipmentId != null) physById[p.equipmentId] = p;
    }
    const servers = {};
    (Array.isArray(equipments) ? equipments : []).forEach((eq, idx) => {
      const { id, name, type, kind, startUnit, spanUnits, liquidCooling, ...eqRest } = eq;
      const uid = id != null ? String(id) : 'srv_' + idx;
      const phys = physById[uid] || {};
      const { equipmentId: _eqid, ...physRest } = phys;
      const coldPlates = {};
      const pairs = liquidCooling && Array.isArray(liquidCooling.cpCuPairs) ? liquidCooling.cpCuPairs : [];
      pairs.forEach((pair, pidx) => {
        const cpUid = pair.id != null ? String(pair.id) : (pair.name != null ? String(pair.name) : 'cp_' + pidx);
        coldPlates[cpUid] = _assignRecord({ uid: cpUid }, pair);
      });
      servers[uid] = {
        uid,
        type: type || kind || 'fanServer',
        geometry: { slotPosition: startUnit, slotOccupation: spanUnits },
        cooling: physRest,
        constructions: { chips: {}, coldPlates },
        meta: { name: name != null ? name : uid, enabled: true },
        ...(liquidCooling ? { liquidCooling } : {}),
        ...eqRest,
      };
    });
    out.constructions = { servers, partitions: Array.isArray(partitions) ? partitions : [], rdhx: null };
    const leftover = _distributePatchPhysics(out.geometry.faces, patchPhysics);
    if (Object.keys(leftover).length) out.cooling.unboundPatchPhysics = leftover;
    _assignRecord(out.cooling, cfdRest);
    _assignRecord(out, rackRest, rest2);
    return out;
  }

  function _migrateAcu(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { role, patchPhysics, ...cfdRest } = cfd;
    const pp = patchPhysics || {};
    const supplyFaces = [], returnFaces = [];
    for (const face of _facesList(out.geometry.faces)) {
      for (const patch of Array.isArray(face.patches) ? face.patches : []) {
        const phys = pp[patch && patch.id];
        if (!phys) continue;
        if (phys.role === 'supply') supplyFaces.push({ faceId: face.id, patchId: patch.id });
        if (phys.role === 'return') returnFaces.push({ faceId: face.id, patchId: patch.id });
      }
    }
    const leftover = _distributePatchPhysics(out.geometry.faces, patchPhysics);
    if (Object.keys(leftover).length) out.cooling.unboundPatchPhysics = leftover;
    out.geometry.supplyFaces = supplyFaces;
    out.geometry.returnFaces = returnFaces;
    out.constructions = { heatExchanger: null, fans: {} };
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest);
  }

  function _migrateCdu(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { liquidCooling, ...rest2 } = rest;
    const lc = liquidCooling || {};
    const { supplyTemperature, supplyPressure, totalFlowRate, servedRacks, advancedMode, advanced, ...lcRest } = lc;
    if (supplyTemperature != null) out.cooling.supplyTemperatureC = supplyTemperature;
    if (supplyPressure != null) out.cooling.supplyPressureKpa = supplyPressure;
    if (totalFlowRate != null) out.cooling.totalFlowRate = totalFlowRate;
    if (advancedMode !== undefined) out.cooling.advancedMode = advancedMode;
    _assignRecord(out.cooling, lcRest);
    const adv = advanced || {};
    out.constructions = {
      heatExchanger: adv.heatExchanger || null,
      pump: adv.pump || null,
      valve: adv.valve || null,
    };
    out.meta.racks = Array.isArray(servedRacks) ? servedRacks : [];
    const { role, ...cfdRest } = cfd;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest2);
  }

  function _migratePipe(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { pipe, ...rest2 } = rest;
    const p = pipe || {};
    const { spec, diameter, waypoints, color, valves, startPortId, endPortId, blindEnds, junctions, ...pipeRest } = p;
    if (waypoints) out.geometry.waypoints = waypoints;
    if (diameter != null) out.geometry.diameterM = diameter;
    if (spec != null) out.cooling.spec = spec;
    out.cooling.loopRole = color || 'unconnected';
    if (valves) out.cooling.valves = valves;
    out.cooling.connection = {
      startPortId: startPortId != null ? startPortId : null,
      endPortId: endPortId != null ? endPortId : null,
      blindEnds: blindEnds || { start: false, end: false },
      junctions: junctions || [],
    };
    _assignRecord(out.cooling, pipeRest);
    const { role, ...cfdRest } = cfd;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest2);
  }

  // Air-side pipe / duct (kind 'pipe', bucket 'pipes'): a swept solid along
  // `geometry.waypoints` (LOCAL to geometry.location). Round pipes carry
  // diameterM, rectangular ones widthM/heightM; `joins` pairs snapped ends.
  function _migrateAirPipe(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { pipe, ...rest2 } = rest;
    const p = pipe || {};
    const { shape, diameterM, widthM, heightM, waypoints, joins, ...pipeRest } = p;
    out.geometry.waypoints = Array.isArray(waypoints) ? waypoints : [];
    out.geometry.shape = shape === 'rect' ? 'rect' : 'round';
    out.geometry.diameterM = diameterM != null ? diameterM : 0.15;
    out.geometry.widthM = widthM != null ? widthM : 0.2;
    out.geometry.heightM = heightM != null ? heightM : 0.2;
    out.geometry.joins = Array.isArray(joins) ? joins : [];
    _assignRecord(out.cooling, pipeRest);
    const { role, ...cfdRest } = cfd;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest2);
  }

  function _migrateJunction(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { junction, ...rest2 } = rest;
    if (junction) {
      const { type, lossCoeff, connectedPipes, ...jRest } = junction;
      if (type != null) out.cooling.type = type;
      if (lossCoeff != null) out.cooling.lossCoeff = lossCoeff;
      if (connectedPipes) out.connectedPipes = connectedPipes;
      _assignRecord(out.cooling, jRest);
    }
    const { role, ...cfdRest } = cfd;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest2);
  }

  function _migrateCustom(m) {
    const { out, cfd, rest } = _commonV4(m);
    const { customSTLBase64, customOffset, solidRegions, ...rest2 } = rest;
    out.customMesh = {
      stlBase64: customSTLBase64,
      offset: customOffset || { x: 0, y: 0, z: 0 },
      solidRegions: solidRegions || [],
    };
    const { role, patchPhysics, ...cfdRest } = cfd;
    const leftover = _distributePatchPhysics(out.geometry.faces, patchPhysics);
    if (Object.keys(leftover).length) out.cooling.unboundPatchPhysics = leftover;
    _assignRecord(out.cooling, cfdRest);
    return _assignRecord(out, rest2);
  }

  if (typeof window !== 'undefined') {
    window.CFDPROJ_SCHEMA_VERSION = SCHEMA_VERSION;
    window.migrateCfdprojV2toV3 = migrateCfdprojV2toV3;
    window.migrateCfdprojV3toV4 = migrateCfdprojV3toV4;
    window.migrateModelV3toV4 = migrateModelV3toV4;
    window.migrateCfdprojToV4 = migrateCfdprojToV4;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      migrateCfdprojV2toV3,
      migrateCfdprojV3toV4,
      migrateModelV3toV4,
      migrateCfdprojToV4,
      CFDPROJ_SCHEMA_VERSION: SCHEMA_VERSION,
    };
  }
})();
