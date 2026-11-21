import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const require = createRequire(import.meta.url);
const V = require('../shared/js/cfdproj_state_v4.js');
const M = require('../shared/js/cfdproj_migrate.js');
const S = require('../shared/js/cfdrun_schema.js');
const E = require('../shared/js/cfdrun_export.js');

// Load a real baseline .cfdproj -> migrate to V4 -> normalize (AD5 harness,
// mirroring test_cfdproj_state_v4.mjs).
function loadV4(rel) {
  const raw = JSON.parse(
    readFileSync(new URL(`../../../case/${rel}.cfdproj`, import.meta.url), 'utf8')
  );
  return V.normalizeStateV4(M.migrateCfdprojToV4(raw));
}

test('assembleInputs: servers carry numeric inputPowerW + string bcMode', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const inputs = E.assembleInputs(st);
  assert.ok(inputs && typeof inputs === 'object');
  // servers is a non-empty object keyed by server uid
  assert.equal(typeof inputs.servers, 'object');
  const serverKeys = Object.keys(inputs.servers);
  assert.ok(serverKeys.length > 0, 'expected at least one server in inputs.servers');
  // Base_leakage server e_2: power 1600, serverBCMode tempRiseAndFlow
  const srv = inputs.servers.e_2;
  assert.ok(srv, 'expected server e_2 in inputs.servers');
  assert.equal(typeof srv.inputPowerW, 'number');
  assert.ok(Number.isFinite(srv.inputPowerW));
  assert.equal(srv.inputPowerW, 1600);
  assert.equal(typeof srv.bcMode, 'string');
  assert.equal(srv.bcMode, 'tempRiseAndFlow');
  assert.equal(typeof srv.airVolumeFlowRateM3s, 'number');
  assert.equal(srv.temperatureRiseC, 20);
  // serverSwitch from meta.enabled
  assert.equal(srv.serverSwitch, true);
  // undefined fields are NOT serialized
  assert.ok(!('inputPowerPercentage' in srv) || srv.inputPowerPercentage !== undefined);
});

test('assembleInputs: acus carry supplyAirTemperatureC + coolingMode', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const inputs = E.assembleInputs(st);
  assert.equal(typeof inputs.acus, 'object');
  const acuKeys = Object.keys(inputs.acus);
  assert.ok(acuKeys.length > 0, 'expected at least one ACU');
  const acu = inputs.acus[acuKeys[0]];
  assert.equal(typeof acu.supplyAirTemperatureC, 'number');
  assert.equal(acu.supplyAirTemperatureC, 15);
  assert.equal(typeof acu.supplyAirVolumeFlowRateM3s, 'number');
  assert.equal(typeof acu.coolingMode, 'string');
  // setPoint / coolant temps present on this ACU's supply patch
  assert.equal(acu.setPointTemperatureC, 15);
  assert.equal(acu.coolantTemperatureC, 7);
  assert.equal(acu.acuSwitch, true);
});

test('assembleInputs: liquid + labels skeletons present', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const inputs = E.assembleInputs(st);
  assert.equal(typeof inputs.liquid, 'object');
  assert.equal(typeof inputs.liquid.enabled, 'boolean');
  assert.equal(inputs.liquid.enabled, false);
  assert.equal(typeof inputs.liquid.inheritResults, 'boolean');
  assert.ok(inputs.labels && typeof inputs.labels.sensorMeasurements === 'object');
  // empty-by-default device sections still present
  for (const k of ['racks', 'cdus', 'partitionWalls', 'pipeSegments']) {
    assert.equal(typeof inputs[k], 'object');
  }
});

test('assembleInputs: Liquid case reports liquid.enabled true', () => {
  const st = loadV4('Liquid/Liquid');
  const inputs = E.assembleInputs(st);
  assert.equal(inputs.liquid.enabled, true);
});

test('assembleGeometryExport: rect solid area = w*h, sanitized patch names', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const W = 0.6, H = 1.2;
  const mockStlMap = {
    // rect patch with known w,h so we can assert exact areaM2 = w*h
    'R1 patch face': {
      solidName: 'R1 patch face',
      stlFileId: 'f_rack',
      objectPath: 'constructions.racks.m_1',
      surfaceUid: 'sfc_1',
      kind: 'rack',
      role: 'wall',
      path: 'constant/triSurface/merged_rack_frame.stl',
      shape: 'rect',
      width: W,
      height: H,
      source: { modelUid: 'm_1', faceId: 'front', patchId: 'p_x' },
    },
    // wall solid: area provided directly in the map
    'wall_body-1': {
      solidName: 'wall_body-1',
      stlFileId: 'f_wall',
      objectPath: 'constructions.walls.m_45',
      surfaceUid: 'sfc_2',
      kind: 'custom',
      role: 'wall',
      path: 'constant/triSurface/room_wall.stl',
      areaM2: 12.5,
      source: { modelUid: 'm_45' },
    },
  };
  const ge = E.assembleGeometryExport(st, mockStlMap);
  assert.equal(ge.coordinateSystem, 'Y-up');
  assert.equal(ge.unit, 'm');
  assert.equal(typeof ge.solids, 'object');
  assert.equal(typeof ge.stlFiles, 'object');

  const solids = Object.values(ge.solids);
  assert.equal(solids.length, 2);

  const rect = solids.find((s) => s.solidName === 'R1 patch face');
  assert.ok(rect, 'rect solid present');
  // patchName sanitized: every non [A-Za-z0-9_] -> _
  assert.equal(rect.patchName, 'R1_patch_face');
  assert.equal(typeof rect.stlFileId, 'string');
  assert.equal(rect.stlFileId, 'f_rack');
  // exact area from state geometry: w*h
  assert.ok(Number.isFinite(rect.areaM2));
  assert.ok(Math.abs(rect.areaM2 - W * H) < 1e-9, `areaM2 ${rect.areaM2} != ${W * H}`);
  assert.equal(rect.role, 'wall');
  assert.equal(rect.objectPath, 'constructions.racks.m_1');
  assert.equal(rect.surfaceUid, 'sfc_1');

  const wall = solids.find((s) => s.solidName === 'wall_body-1');
  assert.ok(wall, 'wall solid present');
  assert.equal(wall.patchName, 'wall_body_1');
  assert.equal(wall.areaM2, 12.5); // passthrough from map

  // distinct stlFiles materialized, with path + kind from the map
  assert.ok('f_rack' in ge.stlFiles);
  assert.ok('f_wall' in ge.stlFiles);
  assert.equal(ge.stlFiles.f_rack.path, 'constant/triSurface/merged_rack_frame.stl');
  assert.equal(ge.stlFiles.f_rack.kind, 'rack');

  // splicing into an empty manifest validates clean
  const m = S.emptyManifest();
  m.runId = 'r1';
  m.sourceProject = { uid: 'u', schemaVersion: 4, projectName: 'p', filePath: null, contentHash: 'h', exportedAt: 'x' };
  m.geometryExport = ge;
  assert.deepEqual(S.validateManifest(m), []);
});

test('assembleGeometryExport: tolerates null stlMap -> empty skeleton (AD2)', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const ge = E.assembleGeometryExport(st, null);
  assert.equal(ge.coordinateSystem, 'Y-up');
  assert.equal(ge.unit, 'm');
  assert.deepEqual(ge.solids, {});
  assert.deepEqual(ge.stlFiles, {});
  const ge2 = E.assembleGeometryExport(st, {});
  assert.deepEqual(ge2.solids, {});
  assert.deepEqual(ge2.stlFiles, {});
});

test('buildSourceProject: schemaVersion 4, 64-hex contentHash, injected exportedAt, deterministic', async () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const sp = await E.buildSourceProject(st, '2026-01-01T00:00:00Z');
  assert.equal(sp.schemaVersion, 4);
  assert.equal(sp.uid, st.uid);
  assert.equal(sp.projectName, st.name);
  assert.match(sp.contentHash, /^[0-9a-f]{64}$/);
  assert.equal(sp.exportedAt, '2026-01-01T00:00:00Z');
  // determinism: same state hashes identically
  const sp2 = await E.buildSourceProject(st, '2026-01-01T00:00:00Z');
  assert.equal(sp2.contentHash, sp.contentHash);
  // exportedAt defaults to null when not injected (no Date.now inside)
  const sp3 = await E.buildSourceProject(st);
  assert.equal(sp3.exportedAt, null);
  assert.equal(sp3.contentHash, sp.contentHash);
});

// ============================================================================
// T2b — translateAcus(state, ctx)
// ============================================================================

test('translateAcus: hand-built fixture — supply binding carries all parameters', () => {
  // Minimal state with one ACU: one supply patch + one return patch.
  const acuUid = 'acu_x';
  const state = {
    constructions: {
      acus: {
        [acuUid]: {
          uid: acuUid,
          geometry: {
            faces: {
              bottom: {
                id: 'bottom',
                patches: [
                  {
                    id: 'p_s',
                    physics: {
                      role: 'supply',
                      temperature: 15,
                      volumeFlowRate: 4,
                      coolingCapacity: 30,       // kW — must be *1000 -> 30000 W
                      coolantTemperature: 7,
                      setPointTemperature: 15,
                      coolingModel: { mode: 'fixedTemp' },
                      // minSupplyTemperature absent -> derived as coolant+6.3 = 13.3
                    },
                  },
                ],
              },
              top: {
                id: 'top',
                patches: [
                  {
                    id: 'p_r',
                    physics: {
                      role: 'return',
                      volumeFlowRate: 4,
                    },
                  },
                ],
              },
            },
            supplyFaces: [{ faceId: 'bottom', patchId: 'p_s' }],
            returnFaces: [{ faceId: 'top', patchId: 'p_r' }],
          },
          meta: { name: 'TestACU', enabled: true },
        },
      },
    },
  };

  // ctx.bySource maps "<acuUid>|<faceId>|<patchId>" -> solid info
  const ctx = {
    bySource: {
      [`${acuUid}|bottom|p_s`]: { patchName: 'ACU_1_Bottom_patch', solidName: 'ACU_1_Bottom_patch', stlFileId: 'f1', areaM2: 0.72, surfaceUid: 'sv_1' },
      [`${acuUid}|top|p_r`]:    { patchName: 'ACU_1_Top_patch',    solidName: 'ACU_1_Top_patch',    stlFileId: 'f1', areaM2: 0.72, surfaceUid: 'sv_2' },
    },
  };

  const result = E.translateAcus(state, ctx);
  assert.ok(result && typeof result.boundaryBindings === 'object', 'result has boundaryBindings');

  const bb = result.boundaryBindings;
  const supplyKey = 'bb_ACU_1_Bottom_patch';
  const returnKey = 'bb_ACU_1_Top_patch';

  assert.ok(supplyKey in bb, `supply binding key ${supplyKey} present`);
  assert.ok(returnKey in bb, `return binding key ${returnKey} present`);

  // --- supply binding ---
  const sup = bb[supplyKey];
  assert.equal(sup.bcType, 'acu_supply');
  assert.equal(sup.role, 'acu_supply');
  assert.equal(sup.patchName, 'ACU_1_Bottom_patch');
  assert.equal(sup.solidName, 'ACU_1_Bottom_patch');
  assert.equal(sup.objectPath, `constructions.acus.${acuUid}`);

  const p = sup.parameters;
  assert.equal(p.temperatureC, 15,          'temperatureC default=15');
  assert.equal(p.flowRateM3s,  4,           'flowRateM3s default=4');
  assert.equal(p.coolingCapacityW, 30000,   'coolingCapacity kW*1000');
  assert.equal(p.coolantTempC, 7,           'coolantTempC');
  assert.ok(Math.abs(p.minSupplyTempC - 13.3) < 1e-9, `minSupplyTempC=coolant+6.3 got ${p.minSupplyTempC}`);
  assert.equal(p.setPointTempC, 15,         'setPointTempC');
  assert.equal(p.returnPatchName, 'ACU_1_Top_patch', 'returnPatchName from first return patch');
  assert.equal(p.coolingModelMode, 'fixedTemp', 'coolingModelMode');
  assert.deepEqual(p.timeVarying, {},        'timeVarying defaults to {}');
  // Fields absent in physics must NOT be written (ratedReturnTempC, coolingPerformanceTable)
  assert.ok(!('ratedReturnTempC' in p),          'ratedReturnTempC absent when not set');
  assert.ok(!('coolingPerformanceTable' in p),    'coolingPerformanceTable absent when not set');

  // --- return binding ---
  const ret = bb[returnKey];
  assert.equal(ret.bcType, 'acu_return');
  assert.equal(ret.role, 'acu_return');
  assert.equal(ret.patchName, 'ACU_1_Top_patch');
  assert.equal(ret.parameters.flowRateM3s, 4);
  assert.deepEqual(ret.parameters.timeVarying, {});
  // return binding has NO supply-only fields
  assert.ok(!('temperatureC' in ret.parameters),      'no temperatureC on return');
  assert.ok(!('returnPatchName' in ret.parameters),   'no returnPatchName on return');
  assert.ok(!('coolingCapacityW' in ret.parameters),  'no coolingCapacityW on return');
});

test('translateAcus: no-solid-export → no binding produced (AD7)', () => {
  // A supply patch with NO bySource hit must produce no binding at all.
  const acuUid = 'acu_y';
  const state = {
    constructions: {
      acus: {
        [acuUid]: {
          uid: acuUid,
          geometry: {
            faces: {
              bottom: {
                id: 'bottom',
                patches: [{ id: 'p_s', physics: { role: 'supply', temperature: 15, volumeFlowRate: 4 } }],
              },
              top: {
                id: 'top',
                patches: [{ id: 'p_r', physics: { role: 'return', volumeFlowRate: 4 } }],
              },
            },
            supplyFaces: [{ faceId: 'bottom', patchId: 'p_s' }],
            returnFaces: [{ faceId: 'top',    patchId: 'p_r' }],
          },
          meta: { name: 'NoExportACU', enabled: true },
        },
      },
    },
  };

  // bySource is EMPTY — neither patch was exported to STL
  const ctx = { bySource: {} };
  const result = E.translateAcus(state, ctx);
  assert.deepEqual(Object.keys(result.boundaryBindings), [],
    'no bindings when bySource has no hits (AD7)');
});

test('translateAcus: real Base_leakage state → ≥2 acu_supply + ≥2 acu_return, validateManifest passes', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const acus = st.constructions && st.constructions.acus;
  assert.ok(acus, 'acus bucket present');

  // Build a synthetic ctx.bySource by enumerating supply/return patches deterministically.
  // Each ACU has supplyFaces / returnFaces listing { faceId, patchId }.
  const bySource = {};
  Object.keys(acus).forEach(function(acuUid) {
    const acu = acus[acuUid];
    const acuName = (acu.meta && acu.meta.name) || acuUid;
    const geom = acu.geometry || {};

    (geom.supplyFaces || []).forEach(function(sf) {
      const key = `${acuUid}|${sf.faceId}|${sf.patchId}`;
      const pn = E.sanitizeName(`${acuName}_Bottom_patch`);
      bySource[key] = { patchName: pn, solidName: pn, stlFileId: 'f_acu', areaM2: 0.72, surfaceUid: `sv_${sf.patchId}` };
    });
    (geom.returnFaces || []).forEach(function(rf) {
      const key = `${acuUid}|${rf.faceId}|${rf.patchId}`;
      const pn = E.sanitizeName(`${acuName}_Top_patch`);
      bySource[key] = { patchName: pn, solidName: pn, stlFileId: 'f_acu', areaM2: 0.72, surfaceUid: `sv_${rf.patchId}` };
    });
  });

  const ctx = { bySource };
  const result = E.translateAcus(st, ctx);
  const bb = result.boundaryBindings;
  const allBindings = Object.values(bb);

  const supplyBindings = allBindings.filter(b => b.bcType === 'acu_supply');
  const returnBindings = allBindings.filter(b => b.bcType === 'acu_return');

  assert.ok(supplyBindings.length >= 2, `expected ≥2 acu_supply, got ${supplyBindings.length}`);
  assert.ok(returnBindings.length >= 2, `expected ≥2 acu_return, got ${returnBindings.length}`);

  // every binding's bcType must be in the allowed set
  const validBcTypes = new Set(['acu_supply', 'acu_return']);
  allBindings.forEach(function(b) {
    assert.ok(validBcTypes.has(b.bcType), `unexpected bcType: ${b.bcType}`);
  });

  // splicing the bindings into an emptyManifest + valid sourceProject must validate clean
  const m = S.emptyManifest();
  m.runId = 'r_bl_acu';
  m.sourceProject = { uid: 'u', schemaVersion: 4, projectName: 'Base_leakage', filePath: null, contentHash: 'a'.repeat(64), exportedAt: '2026-01-01T00:00:00Z' };
  m.boundaryBindings = bb;
  assert.deepEqual(S.validateManifest(m), [], 'validateManifest returns [] with acu bindings');
});

// ============================================================================
// T2c-1 — translateRackServers(state, ctx)
//   fan/nonFan server boundary bindings + per-patch flow split + RDHX override.
//
// Physics constants (must match backend/aircooling/utils/const.py):
//   rho_air = 1.19, air_specific_heat = 1005.
// Join key convention (AD7): for each server face the translator looks up
//   ctx.bySource["<serverUid>|<faceId>|<patchId>"]  for each patch solid,
//   ctx.bySource["<serverUid>|<faceId>|__face__"]    for a whole-face solid,
//   ctx.bySource["<serverUid>|<faceId>|__wall__"]    for the wall remainder.
// Surfaces with no bySource hit are skipped (AD9).
// ============================================================================

const RHO_AIR = 1.19;
const CP_AIR = 1005;

// Build a minimal V4 state with one rack + one server (fan or nonFan).
// `serverFaces` is the server.faces dict; `cooling` is server.cooling.
function mkRackServerState(opts) {
  opts = opts || {};
  const rackUid = opts.rackUid || 'm_1';
  const serverUid = opts.serverUid || 'e_1';
  const rackGeom = Object.assign(
    { size: { x: 0.6, y: 2, z: 0.6 }, slotWidthRatio: 0.8, slotHeight: 0.04445 },
    opts.rackGeom || {}
  );
  const server = {
    uid: serverUid,
    type: opts.type || 'fanServer',
    geometry: { slotPosition: 0, slotOccupation: opts.spanSlots != null ? opts.spanSlots : 1 },
    cooling: opts.cooling || {},
    meta: { name: opts.serverName || 'srv', enabled: true },
    faces: opts.serverFaces || { inlet: { patches: [] }, outlet: { patches: [] } },
  };
  const rackCons = { servers: {} };
  rackCons.servers[serverUid] = server;
  if (opts.rdhx) rackCons.rdhx = opts.rdhx;
  if (opts.extraServers) {
    Object.keys(opts.extraServers).forEach(function (k) { rackCons.servers[k] = opts.extraServers[k]; });
  }
  const rack = { uid: rackUid, geometry: rackGeom, constructions: rackCons, meta: { name: 'Rack' } };
  const racks = {};
  racks[rackUid] = rack;
  const st = { constructions: { racks: racks } };
  if (opts.ambient != null) st.geometry = { temperature: opts.ambient };
  return st;
}

test('translateRackServers: fan-server fixture — inlet/outlet params + area', () => {
  // One rack (W=0.6, slotWidthRatio=0.8, slotHeight=0.04445), one fanServer
  // (power 1000, flow 0.1, powerAndFlow, spanSlots=1). inlet whole-face,
  // outlet 1 patch.
  const st = mkRackServerState({
    rackUid: 'm_1',
    serverUid: 'e_1',
    type: 'fanServer',
    spanSlots: 1,
    cooling: { power: 1000, volumeFlowRate: 0.1, serverBCMode: 'powerAndFlow' },
    serverFaces: {
      inlet: { patches: [] },                       // whole-face inlet solid
      outlet: { patches: [{ id: 'p_1' }] },         // single outlet patch
    },
  });
  const ctx = {
    bySource: {
      'e_1|inlet|__face__': { patchName: 'srv_inlet', solidName: 'srv_inlet', surfaceUid: 'si' },
      'e_1|outlet|p_1':     { patchName: 'srv_outlet', solidName: 'srv_outlet', surfaceUid: 'so' },
    },
  };

  const result = E.translateRackServers(st, ctx);
  const bb = result.boundaryBindings;

  const inlet = bb['bb_srv_inlet'];
  const outlet = bb['bb_srv_outlet'];
  assert.ok(inlet, 'fan_inlet binding present');
  assert.ok(outlet, 'fan_outlet binding present');

  assert.equal(inlet.bcType, 'fan_inlet');
  assert.equal(inlet.role, 'fan_inlet');
  assert.equal(inlet.patchName, 'srv_inlet');
  assert.equal(inlet.objectPath, 'constructions.racks.m_1.servers.e_1');

  // physicalFlowAreaM2 = W * slotWidthRatio * span * unitH = 0.6*0.8*1*0.04445
  const expectedArea = 0.6 * 0.8 * 1 * 0.04445;
  assert.ok(Math.abs(inlet.parameters.physicalFlowAreaM2 - expectedArea) < 1e-12,
    `inlet area ${inlet.parameters.physicalFlowAreaM2} != ${expectedArea}`);
  assert.equal(inlet.parameters.flowRateM3s, 0.1);
  assert.equal(inlet.parameters.powerW, 1000);
  assert.deepEqual(inlet.parameters.timeVarying, {});
  // inlet has NO temperature
  assert.ok(!('temperatureC' in inlet.parameters), 'fan_inlet has no temperatureC');

  // outlet: T = 25 + P/(rho*cp*Q)
  const expectedT = 25 + 1000 / (RHO_AIR * CP_AIR * 0.1);
  assert.equal(outlet.bcType, 'fan_outlet');
  assert.ok(Math.abs(outlet.parameters.temperatureC - expectedT) < 1e-9,
    `outlet T ${outlet.parameters.temperatureC} != ${expectedT}`);
  assert.equal(outlet.parameters.flowRateM3s, 0.1);
  assert.equal(outlet.parameters.powerW, 1000);
  assert.ok(Math.abs(outlet.parameters.physicalFlowAreaM2 - expectedArea) < 1e-12);
});

test('assembleInputs: duplicate server uid across racks throws (fail-fast guard)', () => {
  // Two racks each carry a server with the SAME uid 'e_3'. The bySource join and
  // inputs.servers are uid-keyed, so a duplicate silently collapses and leaves a
  // fan-server patch unbound -> runner generic-T fallthrough at BC build. The
  // translator must reject this at export with an actionable message.
  const mk = (rackUid) => ({
    uid: rackUid,
    geometry: { size: { x: 0.6, y: 2, z: 0.6 }, slotWidthRatio: 0.8, slotHeight: 0.04445 },
    constructions: { servers: { e_3: {
      uid: 'e_3', type: 'fanServer',
      geometry: { slotPosition: 0, slotOccupation: 1 },
      cooling: {}, meta: { name: 'srv', enabled: true },
      faces: { inlet: { patches: [] }, outlet: { patches: [] } },
    } } },
    meta: { name: 'Rack' },
  });
  const st = { constructions: { racks: { m_1: mk('m_1'), m_2: mk('m_2') } } };

  assert.throws(
    () => E.assembleInputs(st),
    /duplicate server uid "e_3".*m_1.*m_2/,
    'duplicate server uid across racks must throw with both rack uids');
  // the standalone guard is also exported and throws the same way
  assert.throws(() => E.assertUniqueServerUids(st), /duplicate server uid/);
});

test('assembleInputs: globally-unique server uids across racks do not throw', () => {
  const mk = (rackUid, serverUid) => ({
    uid: rackUid,
    geometry: { size: { x: 0.6, y: 2, z: 0.6 }, slotWidthRatio: 0.8, slotHeight: 0.04445 },
    constructions: { servers: { [serverUid]: {
      uid: serverUid, type: 'fanServer',
      geometry: { slotPosition: 0, slotOccupation: 1 },
      cooling: {}, meta: { name: 'srv', enabled: true },
      faces: { inlet: { patches: [] }, outlet: { patches: [] } },
    } } },
    meta: { name: 'Rack' },
  });
  const st = { constructions: { racks: { m_1: mk('m_1', 'e_3'), m_2: mk('m_2', 'e_3__m_2') } } };
  assert.doesNotThrow(() => E.assembleInputs(st));
  assert.doesNotThrow(() => E.assertUniqueServerUids(st));
});

test('translateRackServers: per-patch flow split (outlet has 2 patches)', () => {
  const st = mkRackServerState({
    serverUid: 'e_1',
    type: 'fanServer',
    spanSlots: 1,
    cooling: { power: 1000, volumeFlowRate: 0.1, serverBCMode: 'powerAndFlow' },
    serverFaces: {
      inlet: { patches: [] },
      outlet: { patches: [{ id: 'p_1' }, { id: 'p_2' }] },  // TWO outlet patches
    },
  });
  const ctx = {
    bySource: {
      'e_1|inlet|__face__': { patchName: 'srv_inlet', solidName: 'srv_inlet' },
      'e_1|outlet|p_1':     { patchName: 'srv_outlet_p0', solidName: 'srv_outlet_p0' },
      'e_1|outlet|p_2':     { patchName: 'srv_outlet_p1', solidName: 'srv_outlet_p1' },
    },
  };
  const result = E.translateRackServers(st, ctx);
  const bb = result.boundaryBindings;

  const o0 = bb['bb_srv_outlet_p0'];
  const o1 = bb['bb_srv_outlet_p1'];
  assert.ok(o0 && o1, 'both outlet patch bindings present');

  // each patch: Q/N, P/N (N=2)
  assert.equal(o0.parameters.flowRateM3s, 0.05);
  assert.equal(o0.parameters.powerW, 500);
  assert.equal(o1.parameters.flowRateM3s, 0.05);
  assert.equal(o1.parameters.powerW, 500);

  // temperature preserved (un-split): 25 + P/(rho*cp*Q) with the FULL Q,P
  const expectedT = 25 + 1000 / (RHO_AIR * CP_AIR * 0.1);
  assert.ok(Math.abs(o0.parameters.temperatureC - expectedT) < 1e-9);
  assert.ok(Math.abs(o1.parameters.temperatureC - expectedT) < 1e-9);
});

test('translateRackServers: tempRiseAndFlow bcMode → outlet T = 25 + dt', () => {
  const st = mkRackServerState({
    serverUid: 'e_1',
    type: 'fanServer',
    spanSlots: 1,
    cooling: { serverBCMode: 'tempRiseAndFlow', temperatureRise: 10, volumeFlowRate: 0.1 },
    serverFaces: { inlet: { patches: [] }, outlet: { patches: [{ id: 'p_1' }] } },
  });
  const ctx = {
    bySource: {
      'e_1|inlet|__face__': { patchName: 'srv_inlet', solidName: 'srv_inlet' },
      'e_1|outlet|p_1':     { patchName: 'srv_outlet', solidName: 'srv_outlet' },
    },
  };
  const result = E.translateRackServers(st, ctx);
  const bb = result.boundaryBindings;
  // P = rho*cp*Q*dt; T = 25 + P/(rho*cp*Q) = 25 + dt = 35
  const expectedP = RHO_AIR * CP_AIR * 0.1 * 10;
  const expectedT = 25 + expectedP / (RHO_AIR * CP_AIR * 0.1);
  assert.ok(Math.abs(expectedT - 35) < 1e-9, 'sanity: expectedT == 35');
  assert.equal(bb['bb_srv_outlet'].parameters.temperatureC, 35);
  assert.ok(Math.abs(bb['bb_srv_inlet'].parameters.powerW - expectedP) < 1e-9);
});

test('translateRackServers: nonFan heat_flux + fixed_temp per-face', () => {
  // nonFan: inlet bcType nonfan_heat_flux (power 500), outlet nonfan_fixed_temp (40).
  // nonFan bcType derives from cooling.{inletMode,inletHeatType,inletPower,...}.
  const st = mkRackServerState({
    serverUid: 'e_1',
    type: 'nonFan',
    spanSlots: 1,
    rackGeom: { size: { x: 0.6, y: 2, z: 0.6 }, slotWidthRatio: 0.8, slotHeight: 0.04445 },
    cooling: {
      powerMode: 'powered',
      inletMode: 'powered', inletHeatType: 'fixedPower', inletPower: 500,
      outletMode: 'powered', outletHeatType: 'fixedTemp', outletTemperature: 40,
    },
    serverFaces: {
      inlet: { patches: [] },
      outlet: { patches: [] },
    },
  });
  const ctx = {
    bySource: {
      'e_1|inlet|__face__':  { patchName: 'srv_front', solidName: 'srv_front' },
      'e_1|outlet|__face__': { patchName: 'srv_back', solidName: 'srv_back' },
    },
  };
  const result = E.translateRackServers(st, ctx);
  const bb = result.boundaryBindings;

  const inlet = bb['bb_srv_front'];
  const outlet = bb['bb_srv_back'];
  assert.ok(inlet && outlet, 'both nonFan face bindings present');

  // heat_flux = power / (spanSlots * 0.04445 * rackWidth) = 500 / (1*0.04445*0.6)
  const expectedHf = 500 / (1 * 0.04445 * 0.6);
  assert.equal(inlet.bcType, 'nonfan_heat_flux');
  assert.ok(Math.abs(inlet.parameters.heatFluxWm2 - expectedHf) < 1e-6,
    `heatFlux ${inlet.parameters.heatFluxWm2} != ${expectedHf}`);
  assert.ok(!('temperatureC' in inlet.parameters), 'heat_flux face has no temperatureC');

  assert.equal(outlet.bcType, 'nonfan_fixed_temp');
  assert.equal(outlet.parameters.temperatureC, 40);
  assert.ok(!('heatFluxWm2' in outlet.parameters), 'fixed_temp face has no heatFluxWm2');
});

test('translateRackServers: RDHX overrides fan_outlet temperature', () => {
  // Rack with rdhx enabled + one fanServer. The fan_outlet temperatureC must
  // equal the ported compute_rdhx_heat_rejection air_outlet_temp_C.
  const ambient = 22;
  const Q = 0.2, P = 4000;
  const rdhx = { enabled: true, waterFlowRate: 20, waterInletTemp: 25, effectiveness: 0.5 };
  const st = mkRackServerState({
    serverUid: 'e_1',
    type: 'fanServer',
    spanSlots: 4,
    ambient: ambient,
    rdhx: rdhx,
    cooling: { power: P, volumeFlowRate: Q, serverBCMode: 'powerAndFlow' },
    serverFaces: { inlet: { patches: [] }, outlet: { patches: [{ id: 'p_1' }] } },
  });
  const ctx = {
    bySource: {
      'e_1|inlet|__face__': { patchName: 'srv_inlet', solidName: 'srv_inlet' },
      'e_1|outlet|p_1':     { patchName: 'srv_outlet', solidName: 'srv_outlet' },
    },
  };

  // --- replicate the ported compute_rdhx_heat_rejection (historical rdhx.py) ---
  const _RHO_AIR_RDHX = 1.16, _CP_AIR_RDHX = 1006.0, _CP_WATER = 4180.0, WATER_DENSITY = 998.0;
  const total_rack_Q_air = Q;          // sum over fanServers
  const total_rack_power = P;          // sum over all servers
  const T_exhaust = ambient + total_rack_power / (RHO_AIR * CP_AIR * total_rack_Q_air);
  function rdhxReject(airQ, airTin, waterLpm, waterTin, eff) {
    const m_air = _RHO_AIR_RDHX * airQ;
    const m_water = WATER_DENSITY * waterLpm / 60000.0;
    const C_air = m_air * _CP_AIR_RDHX;
    const C_water = m_water * _CP_WATER;
    const C_min = Math.min(C_air, C_water);
    const dT = airTin - waterTin;
    if (dT <= 0 || C_min <= 0) return airTin;
    const Qh = eff * C_min * dT;
    return airTin - Qh / Math.max(C_air, 1e-12);
  }
  const expectedT = rdhxReject(total_rack_Q_air, T_exhaust, 20, 25, 0.5);

  const result = E.translateRackServers(st, ctx);
  const outlet = result.boundaryBindings['bb_srv_outlet'];
  assert.ok(outlet, 'fan_outlet present');
  assert.ok(Math.abs(outlet.parameters.temperatureC - expectedT) < 1e-9,
    `RDHX outlet T ${outlet.parameters.temperatureC} != ${expectedT}`);
  // sanity: the override actually changed the temperature away from the plain 25+P/(rhoCpQ)
  const plainT = 25 + P / (RHO_AIR * CP_AIR * Q);
  assert.ok(Math.abs(outlet.parameters.temperatureC - plainT) > 1e-6, 'RDHX changed the outlet T');
});

test('translateRackServers: Base_leakage real state → exactly 5 fan_inlet + 5 fan_outlet', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const racks = st.constructions.racks;
  assert.ok(racks, 'racks bucket present');

  // Build a deterministic ctx.bySource enumerating each fanServer's inlet/outlet
  // faces + patches with synthetic solid names (mirrors how T3 will build the map).
  const bySource = {};
  Object.keys(racks).forEach(function (ru) {
    const servers = (racks[ru].constructions || {}).servers || {};
    Object.keys(servers).forEach(function (su) {
      const srv = servers[su];
      if (srv.type !== 'fanServer') return;
      const faces = srv.faces || {};
      ['inlet', 'outlet'].forEach(function (fid) {
        const fdef = faces[fid] || { patches: [] };
        const patches = fdef.patches || [];
        if (patches.length > 0) {
          patches.forEach(function (p, i) {
            const key = su + '|' + fid + '|' + p.id;
            const pn = E.sanitizeName(su + '_' + fid + '_p' + i);
            bySource[key] = { patchName: pn, solidName: pn, surfaceUid: 'sv_' + p.id };
          });
          const wk = su + '|' + fid + '|__wall__';
          const wn = E.sanitizeName(su + '_' + fid + '_wall');
          bySource[wk] = { patchName: wn, solidName: wn, surfaceUid: 'sv_' + fid + '_wall' };
        } else {
          const key = su + '|' + fid + '|__face__';
          const pn = E.sanitizeName(su + '_' + fid + '_face');
          bySource[key] = { patchName: pn, solidName: pn, surfaceUid: 'sv_' + fid };
        }
      });
    });
  });

  const result = E.translateRackServers(st, { bySource: bySource });
  const all = Object.values(result.boundaryBindings);
  const fanIn = all.filter(function (b) { return b.bcType === 'fan_inlet'; });
  const fanOut = all.filter(function (b) { return b.bcType === 'fan_outlet'; });
  assert.equal(fanIn.length, 5, `expected 5 fan_inlet, got ${fanIn.length}`);
  assert.equal(fanOut.length, 5, `expected 5 fan_outlet, got ${fanOut.length}`);

  // splice into an emptyManifest -> validateManifest returns []
  const m = S.emptyManifest();
  m.runId = 'r_bl_srv';
  m.sourceProject = { uid: 'u', schemaVersion: 4, projectName: 'Base_leakage', filePath: null, contentHash: 'a'.repeat(64), exportedAt: '2026-01-01T00:00:00Z' };
  m.boundaryBindings = result.boundaryBindings;
  assert.deepEqual(S.validateManifest(m), [], 'validateManifest returns [] with rack-server bindings');
});

// ============================================================================
// T2c-2 — translateRackExtras(state, ctx)
//   frame / rack_partition / through_zone / rack_door porous bindings.
//
// All tests pass a hand-built ctx.stlMap (keyed by solidName) per AD9/AD10.
// ============================================================================

test('translateRackExtras: frame + rack_partition → wall boundaryBindings', () => {
  const state = {};  // extras translator only needs state for through_zone sigma fallback

  const ctx = {
    stlMap: {
      'rack1_frame': {
        solidName: 'rack1_frame',
        patchName: 'rack1_frame',
        stlFileId: 'f_frame',
        objectPath: 'constructions.racks.m_1',
        surfaceUid: 'sv_frame',
        kind: 'frame',
        source: { modelUid: 'm_1' },
      },
      'rack1_partition_1': {
        solidName: 'rack1_partition_1',
        patchName: 'rack1_partition_1',
        stlFileId: 'f_part',
        objectPath: 'constructions.racks.m_1',
        surfaceUid: 'sv_part',
        kind: 'rack_partition',
        source: { modelUid: 'm_1' },
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  assert.ok(result && typeof result.boundaryBindings === 'object', 'result has boundaryBindings');
  assert.ok(typeof result.porousBindings === 'object', 'result has porousBindings');

  // frame -> wall binding
  const frameKey = 'bb_rack1_frame';
  assert.ok(frameKey in result.boundaryBindings, `frame key ${frameKey} present`);
  const fb = result.boundaryBindings[frameKey];
  assert.equal(fb.bcType, 'wall');
  assert.equal(fb.role, 'wall');
  assert.equal(fb.patchName, 'rack1_frame');
  assert.equal(fb.solidName, 'rack1_frame');
  assert.equal(fb.surfaceUid, 'sv_frame');
  assert.deepEqual(fb.parameters, {}, 'frame wall binding has empty parameters');

  // rack_partition -> wall binding
  const partKey = 'bb_rack1_partition_1';
  assert.ok(partKey in result.boundaryBindings, `partition key ${partKey} present`);
  const pb = result.boundaryBindings[partKey];
  assert.equal(pb.bcType, 'wall');
  assert.equal(pb.role, 'wall');
  assert.equal(pb.patchName, 'rack1_partition_1');
  assert.deepEqual(pb.parameters, {}, 'partition wall binding has empty parameters');

  // no porous bindings from these kinds
  assert.deepEqual(Object.keys(result.porousBindings), [], 'no porousBindings for frame/partition');
});

test('translateRackExtras: through_zone with explicit sigma on stlMap entry', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'rack1_through_zone_1': {
        solidName: 'rack1_through_zone_1',
        patchName: 'rack1_through_zone_1',
        stlFileId: 'f_tz',
        objectPath: 'constructions.racks.m_1',
        surfaceUid: 'sv_tz',
        kind: 'through_zone',
        sigma: 0.05,
        source: { modelUid: 'm_1' },
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  const tzKey = 'bb_rack1_through_zone_1';
  assert.ok(tzKey in result.boundaryBindings, `through_zone key ${tzKey} present`);
  const tz = result.boundaryBindings[tzKey];
  assert.equal(tz.bcType, 'through_zone');
  assert.equal(tz.role, 'through_zone');
  assert.equal(tz.patchName, 'rack1_through_zone_1');
  assert.ok(tz.parameters && typeof tz.parameters === 'object', 'through_zone has parameters');
  assert.equal(tz.parameters.sigma, 0.05, 'sigma from stlMap entry');
  assert.deepEqual(Object.keys(result.porousBindings), [], 'through_zone has no porousBindings');
});

test('translateRackExtras: through_zone sigma sourced from state rack.throughSlotSigma', () => {
  // No sigma on the stlMap entry; must fall back to rack.throughSlotSigma via source.modelUid
  const rackUid = 'm_2';
  const state = {
    constructions: {
      racks: {
        [rackUid]: {
          throughSlotSigma: 0.08,
        },
      },
    },
  };
  const ctx = {
    stlMap: {
      'rack2_through_zone': {
        solidName: 'rack2_through_zone',
        patchName: 'rack2_through_zone',
        kind: 'through_zone',
        source: { modelUid: rackUid },
        // no sigma on entry
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  const tz = result.boundaryBindings['bb_rack2_through_zone'];
  assert.ok(tz, 'through_zone binding present');
  assert.equal(tz.parameters.sigma, 0.08, 'sigma from state rack.throughSlotSigma');
});

test('translateRackExtras: through_zone sigma defaults to 0.05 when neither entry nor state has it', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'rack3_through_zone': {
        solidName: 'rack3_through_zone',
        patchName: 'rack3_through_zone',
        kind: 'through_zone',
        source: { modelUid: 'no_such_rack' },
        // no sigma
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  const tz = result.boundaryBindings['bb_rack3_through_zone'];
  assert.ok(tz, 'through_zone binding present');
  assert.equal(tz.parameters.sigma, 0.05, 'sigma defaults to 0.05');
});

test('translateRackExtras: rack_door perforated (sigma=0.64) → master+slave perforatedBaffle', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'rack1_inlet_door': {
        solidName: 'rack1_inlet_door',
        patchName: 'rack1_inlet_door',
        stlFileId: 'f_door',
        objectPath: 'constructions.racks.m_1',
        surfaceUid: 'sv_door',
        kind: 'rack_door',
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 64 },
        source: { modelUid: 'm_1' },
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  assert.deepEqual(Object.keys(result.boundaryBindings), [], 'rack_door produces no boundaryBindings');

  const pb = result.porousBindings;
  const masterKey = 'pb_rack1_inlet_door_master';
  const slaveKey  = 'pb_rack1_inlet_door_slave';
  assert.ok(masterKey in pb, `master key ${masterKey} present`);
  assert.ok(slaveKey  in pb, `slave key  ${slaveKey}  present`);

  const master = pb[masterKey];
  const slave  = pb[slaveKey];

  // type
  assert.equal(master.type, 'perforatedBaffle');
  assert.equal(slave.type,  'perforatedBaffle');

  // patchNames include _master / _slave suffix
  assert.equal(master.patchName, 'rack1_inlet_door_master');
  assert.equal(slave.patchName,  'rack1_inlet_door_slave');

  // sigma = clamp(64/100, 0..1) = 0.64
  assert.ok(Math.abs(master.parameters.sigma - 0.64) < 1e-9, `master sigma ${master.parameters.sigma}`);
  assert.ok(Math.abs(slave.parameters.sigma  - 0.64) < 1e-9, `slave sigma  ${slave.parameters.sigma}`);

  // perforation block carried through
  assert.equal(master.parameters.perforation.openAreaPct, 64);
  assert.equal(master.parameters.perforation.shape, 'circle');
  assert.equal(slave.parameters.perforation.openAreaPct, 64);
});

test('translateRackExtras: rack_door with openAreaPct=0 → wallBaffle master+slave', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'rack1_outlet_door': {
        solidName: 'rack1_outlet_door',
        patchName: 'rack1_outlet_door',
        kind: 'rack_door',
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 0 },
        source: { modelUid: 'm_1' },
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  const pb = result.porousBindings;
  const masterKey = 'pb_rack1_outlet_door_master';
  const slaveKey  = 'pb_rack1_outlet_door_slave';
  assert.ok(masterKey in pb, 'master key present for wallBaffle');
  assert.ok(slaveKey  in pb, 'slave key present for wallBaffle');
  assert.equal(pb[masterKey].type, 'wallBaffle');
  assert.equal(pb[slaveKey].type,  'wallBaffle');
});

test('translateRackExtras: rack_door with no perforation → defaults to openAreaPct=64 → perforatedBaffle σ=0.64', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'rack1_default_door': {
        solidName: 'rack1_default_door',
        patchName: 'rack1_default_door',
        kind: 'rack_door',
        // no perforation key at all
        source: { modelUid: 'm_1' },
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  const pb = result.porousBindings;
  const masterKey = 'pb_rack1_default_door_master';
  assert.ok(masterKey in pb, 'master key present for default door');
  assert.equal(pb[masterKey].type, 'perforatedBaffle', 'default door is perforatedBaffle');
  assert.ok(Math.abs(pb[masterKey].parameters.sigma - 0.64) < 1e-9,
    `default door sigma = 0.64, got ${pb[masterKey].parameters.sigma}`);
  assert.equal(pb[masterKey].parameters.perforation.openAreaPct, 64, 'default perforation openAreaPct=64');
});

test('translateRackExtras: validateManifest passes when boundaryBindings spliced into emptyManifest', () => {
  // through_zone and wall are both valid BC_TYPES
  const state = {};
  const ctx = {
    stlMap: {
      'rack1_frame': {
        solidName: 'rack1_frame', patchName: 'rack1_frame',
        kind: 'frame', source: { modelUid: 'm_1' },
      },
      'rack1_tz': {
        solidName: 'rack1_tz', patchName: 'rack1_tz',
        kind: 'through_zone', sigma: 0.05, source: { modelUid: 'm_1' },
      },
    },
  };

  const result = E.translateRackExtras(state, ctx);
  const m = S.emptyManifest();
  m.runId = 'r_extras';
  m.sourceProject = { uid: 'u', schemaVersion: 4, projectName: 'p', filePath: null, contentHash: 'a'.repeat(64), exportedAt: '2026-01-01T00:00:00Z' };
  m.boundaryBindings = result.boundaryBindings;
  assert.deepEqual(S.validateManifest(m), [], 'validateManifest returns [] with extras bindings');
});

// ============================================================================
// T2d-1 — translateOpenings(state, ctx)
//   floor_opening / floor_remaining / baffle / blanking_panel / room_wall / partition_wall
//
// All tests use a hand-built ctx.stlMap keyed by solidName per AD9/AD10.
// The shared emitPorousBaffleEntry helper (refactored from translateRackExtras'
// rack_door logic) must be exercised here AND must continue to work for doors.
// ============================================================================

// Helper to build a minimal stlMap entry for opening tests.
function mkEntry(solidName, kind, opts) {
  opts = opts || {};
  const patchName = opts.patchName != null ? opts.patchName : solidName;
  const e = {
    solidName: solidName,
    patchName: patchName,
    objectPath: opts.objectPath || ('openings.' + solidName),
    surfaceUid: opts.surfaceUid || ('sv_' + solidName),
    kind: kind,
    source: opts.source || {},
  };
  if (opts.perforation !== undefined) e.perforation = opts.perforation;
  return e;
}

test('translateOpenings: floor_opening perforated (openAreaPct=50) → master+slave perforatedBaffle σ=0.5', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'floor1_opening1': mkEntry('floor1_opening1', 'floor_opening', {
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 50 },
      }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  assert.ok(result && typeof result.porousBindings === 'object', 'result has porousBindings');
  assert.ok(typeof result.boundaryBindings === 'object', 'result has boundaryBindings');

  const pb = result.porousBindings;
  const masterKey = 'pb_floor1_opening1_master';
  const slaveKey  = 'pb_floor1_opening1_slave';
  assert.ok(masterKey in pb, `master key ${masterKey} present`);
  assert.ok(slaveKey  in pb, `slave key ${slaveKey} present`);

  assert.equal(pb[masterKey].type, 'perforatedBaffle');
  assert.equal(pb[slaveKey].type,  'perforatedBaffle');
  assert.equal(pb[masterKey].patchName, 'floor1_opening1_master');
  assert.equal(pb[slaveKey].patchName,  'floor1_opening1_slave');
  assert.ok(Math.abs(pb[masterKey].parameters.sigma - 0.5) < 1e-9,
    `master sigma ${pb[masterKey].parameters.sigma}`);
  assert.ok(Math.abs(pb[slaveKey].parameters.sigma  - 0.5) < 1e-9,
    `slave sigma ${pb[slaveKey].parameters.sigma}`);
  assert.equal(pb[masterKey].parameters.perforation.openAreaPct, 50);
  assert.equal(pb[slaveKey].parameters.perforation.openAreaPct, 50);
});

test('translateOpenings: floor_opening blocked (openAreaPct=0) → master+slave wallBaffle', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'floor1_blocked': mkEntry('floor1_blocked', 'floor_opening', {
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 0 },
      }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  assert.equal(pb['pb_floor1_blocked_master'].type, 'wallBaffle');
  assert.equal(pb['pb_floor1_blocked_slave'].type,  'wallBaffle');
});

test('translateOpenings: floor_opening with no perforation → wallBaffle master+slave', () => {
  // No perforation key → treated as wall (sigma ≤ 0)
  const state = {};
  const ctx = {
    stlMap: {
      'floor1_noperf': mkEntry('floor1_noperf', 'floor_opening'),
      // no perforation key
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  assert.ok('pb_floor1_noperf_master' in pb, 'master present');
  assert.equal(pb['pb_floor1_noperf_master'].type, 'wallBaffle');
  assert.equal(pb['pb_floor1_noperf_slave'].type,  'wallBaffle');
});

test('translateOpenings: floor_remaining perforated (openAreaPct=64) → perforatedBaffle σ=0.64', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'floor1_remaining': mkEntry('floor1_remaining', 'floor_remaining', {
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 64 },
      }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  const masterKey = 'pb_floor1_remaining_master';
  const slaveKey  = 'pb_floor1_remaining_slave';
  assert.ok(masterKey in pb, `master key ${masterKey} present`);
  assert.ok(slaveKey  in pb, `slave key ${slaveKey} present`);
  assert.equal(pb[masterKey].type, 'perforatedBaffle');
  assert.equal(pb[slaveKey].type,  'perforatedBaffle');
  assert.ok(Math.abs(pb[masterKey].parameters.sigma - 0.64) < 1e-9,
    `sigma ${pb[masterKey].parameters.sigma}`);
});

test('translateOpenings: baffle with no perforation → wallBaffle master+slave', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'baffle1': mkEntry('baffle1', 'baffle'),
      // no perforation → wallBaffle
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  assert.ok('pb_baffle1_master' in pb, 'master present');
  assert.equal(pb['pb_baffle1_master'].type, 'wallBaffle');
  assert.equal(pb['pb_baffle1_slave'].type,  'wallBaffle');
});

test('translateOpenings: baffle with openAreaPct=60 → perforatedBaffle σ=0.60', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'baffle2': mkEntry('baffle2', 'baffle', {
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 60 },
      }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  assert.ok('pb_baffle2_master' in pb, 'master present');
  assert.equal(pb['pb_baffle2_master'].type, 'perforatedBaffle');
  assert.ok(Math.abs(pb['pb_baffle2_master'].parameters.sigma - 0.60) < 1e-9,
    `sigma ${pb['pb_baffle2_master'].parameters.sigma}`);
});

test('translateOpenings: blanking_panel (openAreaPct=5) → perforatedBaffle σ=0.05', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'blanking1': mkEntry('blanking1', 'blanking_panel', {
        perforation: { shape: 'circle', holeDiameterMm: 1, openAreaPct: 5 },
      }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  const masterKey = 'pb_blanking1_master';
  const slaveKey  = 'pb_blanking1_slave';
  assert.ok(masterKey in pb, `master key ${masterKey} present`);
  assert.ok(slaveKey  in pb, `slave key ${slaveKey} present`);
  assert.equal(pb[masterKey].type, 'perforatedBaffle');
  assert.equal(pb[slaveKey].type,  'perforatedBaffle');
  assert.ok(Math.abs(pb[masterKey].parameters.sigma - 0.05) < 1e-9,
    `sigma ${pb[masterKey].parameters.sigma}`);
  assert.equal(pb[masterKey].parameters.perforation.openAreaPct, 5);
});

test('translateOpenings: blanking_panel with no perforation → wallBaffle', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'blanking2': mkEntry('blanking2', 'blanking_panel'),
      // no perforation → wallBaffle
    },
  };

  const result = E.translateOpenings(state, ctx);
  const pb = result.porousBindings;
  assert.ok('pb_blanking2_master' in pb, 'master present');
  assert.equal(pb['pb_blanking2_master'].type, 'wallBaffle');
  assert.equal(pb['pb_blanking2_slave'].type,  'wallBaffle');
});

test('translateOpenings: room_wall → boundaryBinding bb_* with bcType=wall', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'Wall_1_Front': mkEntry('Wall_1_Front', 'room_wall', { patchName: 'Wall_1_Front' }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  assert.deepEqual(Object.keys(result.porousBindings), [], 'room_wall has no porousBindings');

  const bb = result.boundaryBindings;
  const key = 'bb_Wall_1_Front';
  assert.ok(key in bb, `boundaryBinding key ${key} present`);
  assert.equal(bb[key].bcType, 'wall');
  assert.equal(bb[key].role,   'wall');
  assert.equal(bb[key].patchName, 'Wall_1_Front');
  assert.deepEqual(bb[key].parameters, {}, 'room_wall has empty parameters');
});

test('translateOpenings: partition_wall → boundaryBinding bb_* with bcType=wall', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'partition1_wall': mkEntry('partition1_wall', 'partition_wall', {
        patchName: 'partition1_wall',
        source: { modelUid: 'pw_1' },
      }),
    },
  };

  const result = E.translateOpenings(state, ctx);
  assert.deepEqual(Object.keys(result.porousBindings), [], 'partition_wall has no porousBindings');

  const bb = result.boundaryBindings;
  const key = 'bb_partition1_wall';
  assert.ok(key in bb, `boundaryBinding key ${key} present`);
  assert.equal(bb[key].bcType, 'wall');
  assert.equal(bb[key].role,   'wall');
  assert.equal(bb[key].patchName, 'partition1_wall');
  assert.deepEqual(bb[key].parameters, {});
});

test('translateOpenings: mixed kinds dispatch correctly (skip unknown/rack kinds)', () => {
  // A mixed stlMap with all T2d-1 kinds + one unknown (rack_server, should be skipped).
  const state = {};
  const ctx = {
    stlMap: {
      'floor1_op1': mkEntry('floor1_op1', 'floor_opening', {
        perforation: { shape: 'circle', holeDiameterMm: 3, openAreaPct: 50 },
      }),
      'Wall_2_Front': mkEntry('Wall_2_Front', 'room_wall'),
      'srv_inlet':    mkEntry('srv_inlet', 'rack_server'),   // must be skipped
      'baffle_x':     mkEntry('baffle_x', 'baffle'),          // no perf → wallBaffle
    },
  };

  const result = E.translateOpenings(state, ctx);
  const bb = result.porousBindings;
  // floor_opening gets master+slave
  assert.ok('pb_floor1_op1_master' in bb, 'floor_opening master present');
  assert.ok('pb_floor1_op1_slave'  in bb, 'floor_opening slave present');
  // baffle gets master+slave (wallBaffle)
  assert.ok('pb_baffle_x_master' in bb, 'baffle master present');
  assert.ok('pb_baffle_x_slave'  in bb, 'baffle slave present');
  // rack_server MUST NOT appear in porousBindings or boundaryBindings
  const allPb = Object.keys(result.porousBindings);
  const allBb = Object.keys(result.boundaryBindings);
  assert.ok(!allPb.some(k => k.includes('srv_inlet')), 'rack_server not in porousBindings');
  assert.ok(!allBb.some(k => k.includes('srv_inlet')), 'rack_server not in boundaryBindings');
  // room_wall appears in boundaryBindings
  assert.ok('bb_Wall_2_Front' in result.boundaryBindings, 'room_wall in boundaryBindings');
});

test('translateOpenings: entry with no patchName → silently skipped (AD9)', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'floor_nopatch': {
        solidName: 'floor_nopatch',
        // patchName is absent
        kind: 'floor_opening',
        perforation: { openAreaPct: 50 },
      },
    },
  };

  const result = E.translateOpenings(state, ctx);
  assert.deepEqual(Object.keys(result.porousBindings), [], 'no patchName → silently skip');
  assert.deepEqual(Object.keys(result.boundaryBindings), []);
});

test('translateOpenings: validateManifest passes for room_wall + partition_wall boundaryBindings', () => {
  const state = {};
  const ctx = {
    stlMap: {
      'Wall_1_Front':      mkEntry('Wall_1_Front', 'room_wall'),
      'pw1_wall_Front':    mkEntry('pw1_wall_Front', 'partition_wall'),
    },
  };

  const result = E.translateOpenings(state, ctx);
  const m = S.emptyManifest();
  m.runId = 'r_openings_wall';
  m.sourceProject = { uid: 'u', schemaVersion: 4, projectName: 'p', filePath: null, contentHash: 'a'.repeat(64), exportedAt: '2026-01-01T00:00:00Z' };
  m.boundaryBindings = result.boundaryBindings;
  assert.deepEqual(S.validateManifest(m), [], 'validateManifest returns [] for wall kinds');
});

// ============================================================================
// T2d-2 — translateThermalExtras(state, ctx)
//   wall_object / heat_source / partition_hole -> boundaryBindings / sourceTerms.
//
// All tests use hand-built ctx.stlMap entries.
// Gold references:
//   wall_object  : stlgen_runner.py:3772-3778 + resolve_wall_object_temp :3132-3149
//   heat_source  : stlgen_runner.py:3780-3801
//   partition_hole: _build_partition_hole_fvoptions_config :1522-1595 +
//                   _darcy_forchheimer :228-246 (called with min_depth=0.001)
// ============================================================================

// Helper: build a minimal stlMap entry for thermal tests.
function mkThermalEntry(solidName, kind, opts) {
  opts = opts || {};
  const e = {
    solidName: solidName,
    patchName: opts.patchName != null ? opts.patchName : solidName,
    objectPath: opts.objectPath || ('constructions.' + solidName),
    surfaceUid: opts.surfaceUid || ('sv_' + solidName),
    kind: kind,
    source: opts.source || {},
  };
  if (opts.physics !== undefined)     e.physics = opts.physics;
  if (opts.objSize !== undefined)     e.objSize = opts.objSize;
  if (opts.heatMode !== undefined)    e.heatMode = opts.heatMode;
  if (opts.fixedTemperature !== undefined) e.fixedTemperature = opts.fixedTemperature;
  if (opts.totalPower !== undefined)  e.totalPower = opts.totalPower;
  if (opts.size !== undefined)        e.size = opts.size;
  if (opts.timeVarying !== undefined) e.timeVarying = opts.timeVarying;
  if (opts.leakageRate !== undefined) e.leakageRate = opts.leakageRate;
  if (opts.depth !== undefined)       e.depth = opts.depth;
  if (opts.origin !== undefined)      e.origin = opts.origin;
  if (opts.e1 !== undefined)          e.e1 = opts.e1;
  if (opts.e2 !== undefined)          e.e2 = opts.e2;
  if (opts.cellzoneName !== undefined) e.cellzoneName = opts.cellzoneName;
  return e;
}

// --- Wall object: fixedTemperature directly set ---
test('translateThermalExtras: wall_object fixedTemperature → bb_* bcType=fixedTemperature, temperatureC=45', () => {
  const state = {};  // no ambient needed: physics.fixedTemperature takes priority
  const patchName = 'wallobj1_patch1';
  const ctx = {
    stlMap: {
      [patchName]: mkThermalEntry(patchName, 'wall_object', {
        patchName: patchName,
        physics: { fixedTemperature: 45 },
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  assert.ok(result && typeof result.boundaryBindings === 'object', 'result has boundaryBindings');
  assert.ok(typeof result.sourceTerms === 'object', 'result has sourceTerms');

  const bbKey = 'bb_' + patchName;
  assert.ok(bbKey in result.boundaryBindings, `binding key ${bbKey} present`);
  const bb = result.boundaryBindings[bbKey];
  assert.equal(bb.bcType, 'fixedTemperature', 'bcType is fixedTemperature');
  assert.equal(bb.role, 'fixed_temperature', 'role is fixed_temperature');
  assert.equal(bb.patchName, patchName);
  assert.equal(bb.parameters.temperatureC, 45, 'temperatureC === 45');
  // no sourceTerms for wall objects
  assert.deepEqual(Object.keys(result.sourceTerms), [], 'no sourceTerms for wall_object');
});

// --- Wall object: power → derived temperature ---
test('translateThermalExtras: wall_object heatPower → T = ambient + power/(10*area)', () => {
  // ambient = 22 (from state.geometry.temperature)
  // objSize {W:1,D:1,H:1} -> area = 2*(1*1 + 1*1 + 1*1) = 6
  // T = 22 + 1000 / (10 * 6) = 22 + 16.666... = 38.666...
  const ambient = 22;
  const power = 1000;
  const W = 1, D = 1, H = 1;
  const expectedArea = 2 * (W * D + W * H + D * H);   // 6
  const expectedT = ambient + power / (10 * expectedArea); // 22 + 16.6667 = 38.6667

  const patchName = 'wallobj2_patch1';
  const state = { geometry: { temperature: ambient } };
  const ctx = {
    stlMap: {
      [patchName]: mkThermalEntry(patchName, 'wall_object', {
        patchName: patchName,
        physics: { heatPower: power },   // no fixedTemperature
        objSize: { W: W, D: D, H: H },
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  const bbKey = 'bb_' + patchName;
  assert.ok(bbKey in result.boundaryBindings, `binding key ${bbKey} present`);
  const bb = result.boundaryBindings[bbKey];
  assert.equal(bb.bcType, 'fixedTemperature', 'bcType is fixedTemperature');
  const gotT = bb.parameters.temperatureC;
  assert.ok(Math.abs(gotT - expectedT) < 1e-9,
    `temperatureC ${gotT} !== expected ${expectedT}`);
});

// --- Wall object: power <= 0 → no binding ---
test('translateThermalExtras: wall_object heatPower<=0 → no binding emitted', () => {
  const patchName = 'wallobj3_patch1';
  const state = {};
  const ctx = {
    stlMap: {
      [patchName]: mkThermalEntry(patchName, 'wall_object', {
        patchName: patchName,
        physics: { heatPower: 0 },
        objSize: { W: 1, D: 1, H: 1 },
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  assert.deepEqual(Object.keys(result.boundaryBindings), [], 'power<=0 → no binding');
  assert.deepEqual(Object.keys(result.sourceTerms), [], 'no sourceTerms either');
});

// --- Heat source: power mode ---
test('translateThermalExtras: heat_source power mode → bb_* bcType=heat_source_flux', () => {
  // size {W:1,D:1,H:1} -> area=6, heatFluxWm2=600/6=100, surfaceAreaM2=6
  const W = 1, D = 1, H = 1;
  const totalPower = 600;
  const expectedArea = 2 * (W * D + W * H + D * H);   // 6
  const expectedFlux = totalPower / expectedArea;       // 100

  const patchName = 'hs1_wall1';
  const state = {};
  const ctx = {
    stlMap: {
      [patchName]: mkThermalEntry(patchName, 'heat_source', {
        patchName: patchName,
        heatMode: 'power',
        totalPower: totalPower,
        size: { W: W, D: D, H: H },
        timeVarying: {},
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  const bbKey = 'bb_' + patchName;
  assert.ok(bbKey in result.boundaryBindings, `binding key ${bbKey} present`);
  const bb = result.boundaryBindings[bbKey];
  assert.equal(bb.bcType, 'heat_source_flux', 'bcType is heat_source_flux');
  assert.ok(Math.abs(bb.parameters.heatFluxWm2 - expectedFlux) < 1e-9,
    `heatFluxWm2 ${bb.parameters.heatFluxWm2} !== ${expectedFlux}`);
  assert.equal(bb.parameters.surfaceAreaM2, expectedArea, 'surfaceAreaM2 = area');
  assert.deepEqual(bb.parameters.timeVarying, {}, 'timeVarying carried through');
  assert.deepEqual(Object.keys(result.sourceTerms), [], 'no sourceTerms for heat_source');
});

// --- Heat source: temperature mode ---
test('translateThermalExtras: heat_source temperature mode → bb_* bcType=fixedTemperature', () => {
  const patchName = 'hs2_wall1';
  const state = {};
  const ctx = {
    stlMap: {
      [patchName]: mkThermalEntry(patchName, 'heat_source', {
        patchName: patchName,
        heatMode: 'temperature',
        fixedTemperature: 50,
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  const bbKey = 'bb_' + patchName;
  assert.ok(bbKey in result.boundaryBindings, `binding key ${bbKey} present`);
  const bb = result.boundaryBindings[bbKey];
  assert.equal(bb.bcType, 'fixedTemperature', 'bcType is fixedTemperature');
  assert.equal(bb.parameters.temperatureC, 50, 'temperatureC === 50');
  assert.ok(!('heatFluxWm2' in bb.parameters), 'no heatFluxWm2 on temperature mode');
});

// --- Partition hole: leakageRate < 100 → sourceTerm ---
test('translateThermalExtras: partition_hole leakageRate=50 → sourceTerm porousZone (Darcy-Forchheimer)', () => {
  // sigma = clamp(50/100, 0..1) = 0.5
  // s = clamp(0.5, 0.01, 0.99) = 0.5
  // depth = 0.1 (>= 0.001 min_depth so no clamp)
  // cd = 0.6
  // f = (1/0.1) * ((1/(0.6*0.5)) - 1)^2
  const leakageRate = 50;
  const depth = 0.1;
  const sigma = leakageRate / 100;  // 0.5
  const s = Math.max(0.01, Math.min(0.99, sigma));
  const cd = 0.6;
  const expectedF = (1 / depth) * Math.pow((1 / (cd * s)) - 1, 2);
  const expectedD = 0;
  const cellzoneName = 'ph1';

  const state = {};
  const ctx = {
    stlMap: {
      [cellzoneName]: mkThermalEntry(cellzoneName, 'partition_hole', {
        leakageRate: leakageRate,
        depth: depth,
        origin: [1, 2, 3],
        e1: [0, 0, 1],
        e2: [0, 1, 0],
        cellzoneName: cellzoneName,
        objectPath: 'partitionWalls.pw1.holes.0',
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  assert.deepEqual(Object.keys(result.boundaryBindings), [], 'no boundaryBindings for partition_hole');

  const stKey = 'st_' + cellzoneName;
  assert.ok(stKey in result.sourceTerms, `sourceTerm key ${stKey} present`);
  const st = result.sourceTerms[stKey];
  assert.equal(st.type, 'porousZone', 'sourceTerm type is porousZone');
  assert.equal(st.region, cellzoneName, 'region = cellzoneName');

  const p = st.parameters;
  assert.ok(Math.abs(p.sigma - 0.5) < 1e-9, `sigma ${p.sigma} !== 0.5`);
  assert.equal(p.d[0], expectedD, 'd[0] === 0');
  assert.equal(p.d[1], 0, 'd[1] === 0');
  assert.equal(p.d[2], 0, 'd[2] === 0');
  assert.ok(Math.abs(p.f[0] - expectedF) < 1e-9,
    `f[0] ${p.f[0]} !== expected ${expectedF}`);
  assert.equal(p.f[1], 0, 'f[1] === 0');
  assert.equal(p.f[2], 0, 'f[2] === 0');
  assert.deepEqual(p.origin, [1, 2, 3], 'origin echoed');
  assert.deepEqual(p.e1, [0, 0, 1], 'e1 echoed');
  assert.deepEqual(p.e2, [0, 1, 0], 'e2 echoed');
});

// --- Partition hole: leakageRate >= 100 → no sourceTerm ---
test('translateThermalExtras: partition_hole leakageRate>=100 → no sourceTerm (gated out)', () => {
  const cellzoneName = 'ph2';
  const state = {};
  const ctx = {
    stlMap: {
      [cellzoneName]: mkThermalEntry(cellzoneName, 'partition_hole', {
        leakageRate: 100,
        depth: 0.1,
        origin: [0, 0, 0],
        e1: [0, 0, 1],
        e2: [0, 1, 0],
        cellzoneName: cellzoneName,
      }),
    },
  };

  const result = E.translateThermalExtras(state, ctx);
  assert.deepEqual(Object.keys(result.sourceTerms), [],
    'leakageRate >= 100 → no sourceTerm');
  assert.deepEqual(Object.keys(result.boundaryBindings), []);
});

// ============================================================================
// T2e — translateMonitors(state)
//   instrumentation.probes -> postprocessConfig.probes
//   runDefaults.simulationConfig.monitoring -> postprocessConfig.surfaceMonitors
//   instrumentation.measurements -> postprocessConfig.labels.sensorMeasurements
//   default outputFields = ['T','U','p_rgh']
// ============================================================================

test('translateMonitors: hand fixture — probes + labels mapped correctly', () => {
  // Minimal state with one probe + one sensor measurement.
  const state = {
    instrumentation: {
      probes: {
        p1: {
          uid: 'p1',
          name: 'P1',
          geometry: { location: { x: 1, y: 2, z: 3 } },
          meta: { name: 'P1', enabled: true },
        },
      },
      measurements: {
        s1: { id: 's1', temperatureC: 24 },
      },
    },
    // no runDefaults.simulationConfig.monitoring → surfaceMonitors = {}
  };

  const result = E.translateMonitors(state);
  assert.ok(result && typeof result.postprocessConfig === 'object',
    'result has postprocessConfig');

  const pc = result.postprocessConfig;
  // Four mandatory keys
  assert.ok('probes' in pc, 'probes key present');
  assert.ok('surfaceMonitors' in pc, 'surfaceMonitors key present');
  assert.ok('outputFields' in pc, 'outputFields key present');
  assert.ok('labels' in pc, 'labels key present');

  // probe p1 mapped with correct fields
  assert.ok('p1' in pc.probes, 'probe p1 present');
  const probe = pc.probes.p1;
  assert.equal(probe.name, 'P1', 'probe name === P1');
  assert.deepEqual(probe.location, { x: 1, y: 2, z: 3 },
    'probe location deep-equals {x:1,y:2,z:3}');
  assert.equal(probe.enabled, true, 'probe enabled from meta.enabled');

  // surfaceMonitors empty (absent in state)
  assert.deepEqual(pc.surfaceMonitors, {}, 'surfaceMonitors = {} when absent');

  // outputFields default
  assert.deepEqual(pc.outputFields, ['T', 'U', 'p_rgh'], 'default outputFields');

  // labels.sensorMeasurements echoed from measurements
  assert.ok(pc.labels && typeof pc.labels.sensorMeasurements === 'object',
    'labels.sensorMeasurements present');
  assert.equal(pc.labels.sensorMeasurements.s1.temperatureC, 24,
    'sensorMeasurement s1.temperatureC === 24');
});

test('translateMonitors: real Base_leakage V4 state — 4 keys + validateManifest passes', () => {
  const st = loadV4('Base_leakage/Base_leakage');
  const result = E.translateMonitors(st);
  const pc = result.postprocessConfig;

  // All 4 keys present
  assert.ok('probes' in pc, 'probes present');
  assert.ok('surfaceMonitors' in pc, 'surfaceMonitors present');
  assert.ok('outputFields' in pc, 'outputFields present');
  assert.ok('labels' in pc, 'labels present');

  // Base_leakage has 4 probes in instrumentation.probes
  const probeKeys = Object.keys(pc.probes);
  assert.ok(probeKeys.length >= 1, `expected >=1 probe, got ${probeKeys.length}`);

  // every probe has name, location, enabled
  probeKeys.forEach(function(k) {
    const p = pc.probes[k];
    assert.ok(typeof p.name === 'string', `probe ${k} has name`);
    assert.ok(p.location && typeof p.location.x === 'number', `probe ${k} has location.x`);
    assert.ok(typeof p.enabled === 'boolean', `probe ${k} has enabled`);
  });

  // validateManifest passes when spliced into an emptyManifest
  const m = S.emptyManifest();
  m.runId = 'r_monitors';
  m.sourceProject = {
    uid: 'u', schemaVersion: 4, projectName: 'Base_leakage',
    filePath: null, contentHash: 'a'.repeat(64), exportedAt: '2026-01-01T00:00:00Z',
  };
  m.postprocessConfig = pc;
  assert.deepEqual(S.validateManifest(m), [],
    'validateManifest returns [] with postprocessConfig spliced in');
});

test('translateMonitors: empty state ({}) → skeleton without throwing', () => {
  // Must not throw; must return the 4-key skeleton with empty dicts + default outputFields.
  let result;
  assert.doesNotThrow(function() { result = E.translateMonitors({}); },
    'translateMonitors({}) must not throw');
  const pc = result.postprocessConfig;
  assert.ok('probes' in pc, 'probes key present');
  assert.ok('surfaceMonitors' in pc, 'surfaceMonitors key present');
  assert.ok('outputFields' in pc, 'outputFields key present');
  assert.ok('labels' in pc, 'labels key present');
  assert.deepEqual(pc.probes, {}, 'empty probes');
  assert.deepEqual(pc.surfaceMonitors, {}, 'empty surfaceMonitors');
  assert.deepEqual(pc.outputFields, ['T', 'U', 'p_rgh'], 'default outputFields');
  assert.ok(pc.labels && typeof pc.labels.sensorMeasurements === 'object',
    'labels.sensorMeasurements is object');
});

// ============================================================================
// T2f — buildManifest(state, stlMap, exportedAt)  (CAPSTONE orchestrator)
//   Assembles emptyManifest(), builds the bySource index from stlMap, calls every
//   translator, merges their fragments, mirrors run config (deep-copied), and
//   validates. Async (contentHash + WebCrypto). Returns the full 13-key manifest.
// ============================================================================

// The 13 canonical top-level keys (= Object.keys(emptyManifest()) sorted).
const CANONICAL_KEYS = [
  'artifacts', 'boundaryBindings', 'geometryExport', 'inputs', 'meshConfig',
  'name', 'porousBindings', 'postprocessConfig', 'runId', 'schemaVersion',
  'simulationConfig', 'sourceProject', 'sourceTerms',
].sort();

// 5-case structural: each baseline .cfdproj builds a valid V2 manifest with
// stlMap=null (bindings empty — that flow is tested separately below).
for (const rel of [
  'Base_leakage/Base_leakage',
  'Shinaide/Shinaide',
  'CMI-China/CMI_China',
  'STDCT/STDCT',
  'Liquid/Liquid',
]) {
  test(`buildManifest: ${rel} → valid V2 manifest (stlMap=null)`, async () => {
    const st = loadV4(rel);
    let manifest;
    await assert.doesNotReject(
      async () => { manifest = await E.buildManifest(st, null, '2026-01-01T00:00:00Z'); },
      `buildManifest must not reject for ${rel}`
    );
    // 13 canonical top-level keys
    assert.deepEqual(Object.keys(manifest).sort(), CANONICAL_KEYS,
      `manifest top-level keys for ${rel}`);
    // schemaVersion frozen at 2
    assert.equal(manifest.schemaVersion, 2, 'schemaVersion === 2');
    // structurally valid
    assert.deepEqual(S.validateManifest(manifest), [],
      `validateManifest returns [] for ${rel}`);
    // sourceProject contentHash is a 64-hex SHA-256
    assert.ok(manifest.sourceProject && typeof manifest.sourceProject === 'object',
      'sourceProject present');
    assert.match(manifest.sourceProject.contentHash, /^[0-9a-f]{64}$/,
      'contentHash is 64-hex');
    assert.equal(manifest.sourceProject.exportedAt, '2026-01-01T00:00:00Z',
      'exportedAt threaded through');
    // inputs sub-dicts are objects
    assert.equal(typeof manifest.inputs.servers, 'object', 'inputs.servers object');
    assert.equal(typeof manifest.inputs.acus, 'object', 'inputs.acus object');
    // runId derived from exportedAt (deterministic — no Date.now/Math.random)
    assert.equal(manifest.runId, 'run_2026-01-01T00:00:00Z', 'runId derived from exportedAt');
    // geometryExport empty solids with null stlMap (skeleton)
    assert.deepEqual(manifest.geometryExport.solids, {}, 'no solids with stlMap=null');
  });
}

// Binding-flow with a mock stlMap: one ACU (1 supply + 1 return patch) joined
// through bySource + a room_wall entry. buildManifest must wire ≥3 bindings.
test('buildManifest: mock stlMap wires acu_supply + acu_return + wall bindings', async () => {
  const acuUid = 'acu_m';
  const fid = 'bottom';      // supply face id
  const fidR = 'top';        // return face id
  const pidS = 'p_s';        // supply patch id
  const pidR = 'p_r';        // return patch id

  const state = {
    uid: 'proj_m',
    schemaVersion: 4,
    name: 'MockACUProject',
    constructions: {
      acus: {
        [acuUid]: {
          uid: acuUid,
          geometry: {
            faces: {
              [fid]: {
                id: fid,
                patches: [{
                  id: pidS,
                  physics: { role: 'supply', temperature: 15, volumeFlowRate: 4 },
                }],
              },
              [fidR]: {
                id: fidR,
                patches: [{
                  id: pidR,
                  physics: { role: 'return', volumeFlowRate: 4 },
                }],
              },
            },
            supplyFaces: [{ faceId: fid, patchId: pidS }],
            returnFaces: [{ faceId: fidR, patchId: pidR }],
          },
          meta: { name: 'MockACU', enabled: true },
        },
      },
    },
  };

  // stlMap keyed by solidName. The two ACU patch solids carry source:{modelUid,
  // faceId, patchId}; the wall solid is a room_wall (no source needed).
  const stlMap = {
    'ACU_M_Supply': {
      solidName: 'ACU_M_Supply',
      patchName: 'ACU_M_Supply',
      stlFileId: 'f_acu',
      objectPath: `constructions.acus.${acuUid}`,
      surfaceUid: 'sv_s',
      kind: 'acu',
      source: { modelUid: acuUid, faceId: fid, patchId: pidS },
    },
    'ACU_M_Return': {
      solidName: 'ACU_M_Return',
      patchName: 'ACU_M_Return',
      stlFileId: 'f_acu',
      objectPath: `constructions.acus.${acuUid}`,
      surfaceUid: 'sv_r',
      kind: 'acu',
      source: { modelUid: acuUid, faceId: fidR, patchId: pidR },
    },
    'Wall_1_Front': {
      solidName: 'Wall_1_Front',
      patchName: 'Wall_1_Front',
      stlFileId: 'f_wall',
      objectPath: 'geometry.walls.0',
      surfaceUid: 'sv_w',
      kind: 'room_wall',
      source: { modelUid: 'w_1' },
    },
  };

  const manifest = await E.buildManifest(state, stlMap, '2026-01-01T00:00:00Z');

  // structurally valid
  assert.deepEqual(S.validateManifest(manifest), [], 'validateManifest returns []');

  const bb = manifest.boundaryBindings;
  // supply + return from translateAcus, wall from translateOpenings → ≥3
  assert.ok('bb_ACU_M_Supply' in bb, 'acu_supply binding present');
  assert.ok('bb_ACU_M_Return' in bb, 'acu_return binding present');
  assert.ok('bb_Wall_1_Front' in bb, 'room_wall binding present');
  assert.ok(Object.keys(bb).length >= 3, `≥3 boundaryBindings, got ${Object.keys(bb).length}`);

  // bcTypes correct
  assert.equal(bb['bb_ACU_M_Supply'].bcType, 'acu_supply');
  assert.equal(bb['bb_ACU_M_Return'].bcType, 'acu_return');
  assert.equal(bb['bb_Wall_1_Front'].bcType, 'wall');

  // supply carries the resolved return patch name (the join worked)
  assert.equal(bb['bb_ACU_M_Supply'].parameters.returnPatchName, 'ACU_M_Return',
    'supply returnPatchName resolved through bySource');

  // geometryExport materialized all 3 solids
  const solids = Object.values(manifest.geometryExport.solids);
  assert.equal(solids.length, 3, 'all 3 solids in geometryExport');
});

// meshConfig / simulationConfig mirror — declared-only pass-through, deep-copied
// so the manifest never aliases live state.
test('buildManifest: mirrors meshConfig/simulationConfig, deep-copied (no aliasing)', async () => {
  const state = {
    uid: 'proj_cfg',
    schemaVersion: 4,
    name: 'CfgMirror',
    constructions: {},
    runDefaults: {
      meshConfig: { baseSize: 0.1, refinementLevel: [1, 2] },
      simulationConfig: { solverType: 'steady', endTime: 500 },
    },
  };

  const manifest = await E.buildManifest(state, null, '2026-01-01T00:00:00Z');

  assert.equal(manifest.meshConfig.baseSize, 0.1, 'meshConfig.baseSize mirrored');
  assert.deepEqual(manifest.meshConfig.refinementLevel, [1, 2],
    'meshConfig.refinementLevel mirrored');
  assert.equal(manifest.simulationConfig.solverType, 'steady',
    'simulationConfig.solverType mirrored');
  assert.equal(manifest.simulationConfig.endTime, 500, 'simulationConfig.endTime mirrored');

  // deep-copy: mutating the manifest must NOT mutate live state.
  manifest.meshConfig.baseSize = 999;
  manifest.meshConfig.refinementLevel.push(99);
  manifest.simulationConfig.solverType = 'transient';
  assert.equal(state.runDefaults.meshConfig.baseSize, 0.1,
    'state.runDefaults.meshConfig.baseSize NOT mutated (deep-copy)');
  assert.deepEqual(state.runDefaults.meshConfig.refinementLevel, [1, 2],
    'state.runDefaults.meshConfig.refinementLevel array NOT mutated');
  assert.equal(state.runDefaults.simulationConfig.solverType, 'steady',
    'state.runDefaults.simulationConfig NOT mutated');

  // validates
  assert.deepEqual(S.validateManifest(manifest), [], 'validateManifest returns []');
});

// missing runDefaults → meshConfig/simulationConfig default to {} (not undefined).
test('buildManifest: missing runDefaults → meshConfig/simulationConfig = {}', async () => {
  const state = { uid: 'p', schemaVersion: 4, name: 'NoRunDefaults', constructions: {} };
  const manifest = await E.buildManifest(state, null, '2026-01-01T00:00:00Z');
  assert.deepEqual(manifest.meshConfig, {}, 'meshConfig defaults to {}');
  assert.deepEqual(manifest.simulationConfig, {}, 'simulationConfig defaults to {}');
  assert.deepEqual(S.validateManifest(manifest), [], 'validateManifest returns []');
});

test('stable project hash retains JSON __proto__ keys instead of dropping them', () => {
  const value = JSON.parse('{"__proto__":{"v":1},"ordinary":2}');
  assert.equal(E.stableStringify(value), '{"__proto__":{"v":1},"ordinary":2}');
});
