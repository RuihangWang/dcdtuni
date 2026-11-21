import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const require = createRequire(import.meta.url);
const V = require('../shared/js/cfdproj_state_v4.js');
const S = require('../shared/js/cfdproj_schema.js');
const M = require('../shared/js/cfdproj_migrate.js');

test('bucket API: add/find/all/remove across buckets', () => {
  const st = V.getInitialStateV4();
  V.addConstruction(st, { uid: 'm_1', kind: 'box', meta:{}, geometry:{}, cooling:{} });
  V.addConstruction(st, { uid: 'm_2', kind: 'rack', meta:{}, geometry:{}, cooling:{} });
  assert.equal(V.bucketForKind('heat_source'), 'boxes');   // legacy box-subtype
  assert.equal(V.allModels(st).length, 2);
  assert.equal(V.findConstruction(st, 'm_2').kind, 'rack');
  V.removeConstruction(st, 'm_1');
  assert.equal(V.allModels(st).length, 1);
  assert.ok(!('m_1' in st.constructions.boxes));
});

test('getInitialStateV4: V4-shaped, validates clean, no ui key', () => {
  const st = V.getInitialStateV4();
  assert.equal(st.schemaVersion, 4);
  assert.ok(st.constructions && typeof st.constructions.racks === 'object');
  assert.ok(!('ui' in st));
  assert.ok(!('construction' in st));   // the v3 array name is gone
  assert.ok(st.runDefaults && st.runDefaults.meshConfig && st.runDefaults.simulationConfig);
  st.uid = 'proj_seed';                  // a fresh doc needs a uid to validate
  assert.deepEqual(S.validateDocument(st), []);
});

// The transitional per-model + document-level alias layer was removed in T15i after a runtime
// hit-counter proved zero live consumers across a comprehensive 5-case battery. The tests
// below assert the V4-native shape that is now the single truth:
// per-model fields live at uid/meta.*/geometry.*/cooling.*/constructions.*; document-level
// state lives at geometry.*/meta.casePath/models.*/runDefaults.*/instrumentation.*; and the
// only non-enumerable decoration normalizeStateV4/finalizeNewModelV4 add is the working `kind`.

test('normalizeStateV4: no alias decoration, only non-enumerable working kind', () => {
  const raw = JSON.parse(readFileSync(new URL('../../../case/Base_leakage/Base_leakage.cfdproj', import.meta.url),'utf8'));
  const st = V.normalizeStateV4(M.migrateCfdprojToV4(raw));
  // V4-native document shape (no construction array, no meta.room, no simulation proxy)
  assert.ok(!('construction' in st));
  assert.ok(st.constructions && typeof st.constructions === 'object');
  assert.ok(!('room' in st.meta));
  assert.ok(!('simulation' in st));
  assert.ok(!('probeData' in st) && !('sensorData' in st) && !('liquidCooling' in st));
  // meta + instrumentation always present (formerly lazily created by the alias layer)
  assert.ok(st.meta && typeof st.meta === 'object');
  assert.ok(st.instrumentation && typeof st.instrumentation.probes === 'object'
            && typeof st.instrumentation.measurements === 'object');
  // every model carries a non-enumerable working `kind` and NO alias props
  const m = V.allModels(st)[0];
  assert.ok(m.kind);
  assert.equal(Object.prototype.propertyIsEnumerable.call(m, 'kind'), false);
  assert.ok(!('anchor' in m) && !('base' in m) && !('cfd' in m) && !('id' in m) && !('faces' in m));
  // clean serialize: kind never re-serializes
  assert.ok(!('kind' in JSON.parse(JSON.stringify(m))));
});

test('normalizeStateV4: 5 cases normalize, validate, kind restored, V4-native', () => {
  for (const [rel, n] of [['Base_leakage/Base_leakage',16],['Shinaide/Shinaide',29],
       ['CMI-China/CMI_China',37],['STDCT/STDCT',44],['Liquid/Liquid',13]]) {
    const raw = JSON.parse(readFileSync(new URL(`../../../case/${rel}.cfdproj`, import.meta.url),'utf8'));
    const v4 = M.migrateCfdprojToV4(raw);
    const st = V.normalizeStateV4(v4);
    assert.equal(st.schemaVersion, 4, rel);
    assert.deepEqual(S.validateDocument(st), [], rel);
    assert.equal(V.allModels(st).length, n, rel);
    const m = V.allModels(st)[0];
    // working kind is non-enumerable; no alias props remain
    assert.ok(Object.getOwnPropertyDescriptor(m, 'kind') && !Object.getOwnPropertyDescriptor(m, 'kind').enumerable, rel);
    assert.ok(!('anchor' in m) && !('base' in m), rel);
  }
});

test('normalizeStateV4: rack lands in servers bucket (V4-native, no equipments alias)', () => {
  // synthetic v3 doc with a rack: config + 2 equipments + a door
  const v3 = { schemaVersion:3,
    meta:{projectName:'T',exportFileName:'T',inputUnit:'m',description:'',
          room:{bounds:null,polygon:null,height:3,temperature:22},case:{path:'/tmp'}},
    model:{racks:{},acus:{}},
    construction:[{ id:'r1', kind:'rack', name:'R1', enabled:true,
      anchor:{x:0,y:0,z:0}, base:{W:0.6,H:2,D:1}, faces:{},
      cfd:{ servers:[{equipmentId:'e1',powerW:300,tempC:25}] },
      rack:{ unitCount:42, unitHeight:0.04445, coolingType:'air', deletedFaces:['x'],
             inletDoor:{ perforated:{shape:'circle'} },
             equipments:[ {id:'e1',name:'srv1',kind:'fanServer',startUnit:1,spanUnits:2},
                          {id:'e2',name:'srv2',kind:'blank',startUnit:5,spanUnits:1} ] } }],
    probeData:[], sensorData:[], simulation:{}, liquidCooling:{enabled:false}, ui:{} };
  const st = V.normalizeStateV4(M.migrateCfdprojToV4(v3));
  const r = V.allModels(st).find(m => m.kind === 'rack');
  // rack config now lives V4-native: geometry.slot, cooling.type, etc. (no m.rack proxy)
  assert.ok(!('rack' in r));
  assert.equal(r.geometry.slot, 42);
  assert.equal(r.cooling.type, 'air');
  // servers live V4-native in constructions.servers (no m.equipments array bridge)
  assert.ok(!('equipments' in r));
  assert.equal(Object.keys(r.constructions.servers).length, 2);
  assert.ok('e1' in r.constructions.servers);
  assert.equal(r.constructions.servers.e1.geometry.slotPosition, 1);
  assert.equal(r.constructions.servers.e1.geometry.slotOccupation, 2);
  assert.equal(r.constructions.servers.e1.cooling.powerW, 300);   // physics on server.cooling
  // door reconstructed onto geometry.faces (array form for racks)
  const inletDoor = (Array.isArray(r.geometry.faces) ? r.geometry.faces : [])
    .find(f => f && f.uid === 'inletDoor' && f.role === 'door');
  assert.ok(inletDoor && inletDoor.perforation);
});

test('normalizeStateV4: pipe + junction land V4-native (no m.pipe / m.junction proxies)', () => {
  const v3 = { schemaVersion:3,
    meta:{projectName:'T',exportFileName:'T',inputUnit:'m',description:'',
          room:{bounds:null,polygon:null,height:3,temperature:22},case:{path:'/tmp'}},
    model:{racks:{},acus:{}},
    construction:[
      { id:'p1', kind:'pipe_segment', name:'P1', enabled:true, anchor:{x:0,y:0,z:0},
        cfd:{ role:'pipe' },
        pipe:{ spec:'DN50', diameter:0.1, waypoints:[{x:0,y:0,z:0},{x:1,y:0,z:0}],
               color:'supply', valves:[{at:0.5}], startPortId:'a', endPortId:'b',
               blindEnds:{start:false,end:true}, junctions:['j1'], customPipeField:7 } },
      { id:'j1', kind:'pipe_junction', name:'J1', enabled:true, anchor:{x:1,y:0,z:0},
        cfd:{ role:'junction' },
        junction:{ type:'tee', lossCoeff:0.5, connectedPipes:['p1'], extraJField:9 } },
    ],
    probeData:[], sensorData:[], simulation:{}, liquidCooling:{enabled:false}, ui:{} };
  const st = V.normalizeStateV4(M.migrateCfdprojToV4(v3));
  const p = V.allModels(st).find(m => m.kind === 'pipe_segment');
  const j = V.allModels(st).find(m => m.kind === 'pipe_junction');
  // pipe fields scattered V4-native (no m.pipe proxy)
  assert.ok(!('pipe' in p));
  assert.equal(p.geometry.waypoints.length, 2);
  assert.equal(p.geometry.diameterM, 0.1);
  assert.equal(p.cooling.spec, 'DN50');
  assert.equal(p.cooling.loopRole, 'supply');
  assert.equal(p.cooling.connection.startPortId, 'a');
  assert.equal(p.cooling.connection.endPortId, 'b');
  assert.equal(p.cooling.connection.blindEnds.end, true);
  // junction fields scattered V4-native (no m.junction proxy)
  assert.ok(!('junction' in j));
  assert.equal(j.cooling.type, 'tee');
  assert.equal(j.cooling.lossCoeff, 0.5);
  assert.deepEqual(j.connectedPipes, ['p1']);   // top-level, not under cooling
});

test('normalizeStateV4: cdu lands V4-native (no m.cdu liquidCooling proxy)', () => {
  const v3 = { schemaVersion:3,
    meta:{projectName:'T',exportFileName:'T',inputUnit:'m',description:'',
          room:{bounds:null,polygon:null,height:3,temperature:22},case:{path:'/tmp'}},
    model:{racks:{},acus:{},cdus:{}},
    construction:[
      { id:'c1', kind:'cdu', name:'C1', enabled:true, anchor:{x:0,y:0,z:0}, base:{W:1,H:2,D:1},
        cfd:{ role:'cdu' },
        liquidCooling:{ supplyTemperature:35, supplyPressure:250, totalFlowRate:60,
          servedRacks:['R1'], advancedMode:true,
          advanced:{ pump:{specName:'p',rpm:3000}, heatExchanger:{specName:'hx'}, valve:{specName:'v',opening:1.0} },
          customLcField:5 } },
    ],
    probeData:[], sensorData:[], simulation:{}, liquidCooling:{enabled:false}, ui:{} };
  const st = V.normalizeStateV4(M.migrateCfdprojToV4(v3));
  const c = V.allModels(st).find(m => m.kind === 'cdu');
  // cdu liquid params scattered V4-native (no m.liquidCooling proxy)
  assert.ok(!('liquidCooling' in c));
  assert.equal(c.cooling.supplyTemperatureC, 35);
  assert.equal(c.cooling.supplyPressureKpa, 250);
  assert.equal(c.cooling.totalFlowRate, 60);
  assert.equal(c.cooling.advancedMode, true);
  assert.deepEqual(c.meta.racks, ['R1']);                 // served racks on meta.racks
  assert.equal(c.constructions.pump.rpm, 3000);           // advanced specs in constructions
  assert.equal(c.constructions.heatExchanger.specName, 'hx');
});

test('patch-physics helpers: map / find / get / ensure / delete (V4-native)', () => {
  const model = {
    kind: 'acu',
    geometry: { faces: { front: { patches: [
      { id: 'p1', physics: { role: 'supply', volumeFlowRate: 4, temperature: 15 } },
      { id: 'p2', physics: { role: 'return', volumeFlowRate: 4 } },
      { id: 'p3' },                       // patch with no physics yet
    ] } } },
    cooling: { unboundPatchPhysics: { orphan: { role: 'wall' } } },
  };
  // patchPhysicsMap: bound (live) + unbound merged; patch without physics omitted
  const map = V.patchPhysicsMap(model);
  assert.equal(map.p1.role, 'supply');
  assert.equal(map.p2.role, 'return');
  assert.ok(!('p3' in map));
  assert.equal(map.orphan.role, 'wall');
  // bound entries are live refs — mutation writes through to the patch
  map.p1.volumeFlowRate = 9;
  assert.equal(model.geometry.faces.front.patches[0].physics.volumeFlowRate, 9);
  // findModelPatch
  assert.equal(V.findModelPatch(model, 'p2').physics.role, 'return');
  assert.equal(V.findModelPatch(model, 'nope'), null);
  // getPatchPhysics
  assert.equal(V.getPatchPhysics(model, 'p1').role, 'supply');
  assert.equal(V.getPatchPhysics(model, 'p3'), null);          // patch exists, no physics
  assert.equal(V.getPatchPhysics(model, 'orphan').role, 'wall'); // unbound
  assert.equal(V.getPatchPhysics(model, 'ghost'), null);        // nothing
  // ensurePatchPhysics: seeds defaults onto the real patch; returns live ref
  const phys = V.ensurePatchPhysics(model, 'p3', { role: 'wall', porosity: 0 });
  assert.equal(phys.role, 'wall');
  phys.role = 'supply';
  assert.equal(model.geometry.faces.front.patches[2].physics.role, 'supply');
  // ensure on an existing physics keeps it
  assert.equal(V.ensurePatchPhysics(model, 'p1', { role: 'wall' }).role, 'supply');
  // ensure for a non-existent patch -> unbound
  const ub = V.ensurePatchPhysics(model, 'ghost', { leakageRate: 100 });
  assert.equal(ub.leakageRate, 100);
  assert.equal(model.cooling.unboundPatchPhysics.ghost.leakageRate, 100);
  // deletePatchPhysics: clears unbound; bound stays on patch unless removed by caller
  V.deletePatchPhysics(model, 'orphan');
  assert.ok(!('orphan' in model.cooling.unboundPatchPhysics));
  V.deletePatchPhysics(model, 'p2');
  assert.equal(model.geometry.faces.front.patches[1].physics, undefined);
});

test('patch-physics helpers: faces stored as an array', () => {
  const model = { geometry: { faces: [ { patches: [ { id: 'a', physics: { x: 1 } } ] } ] } };
  assert.equal(V.patchPhysicsMap(model).a.x, 1);
  assert.equal(V.findModelPatch(model, 'a').physics.x, 1);
});

test('finalizeNewModelV4: box seed -> loaded-shape V4 model (no aliases)', () => {
  const boxSeed = { id: 'box_f1', kind: 'box', name: 'B', enabled: true, opacity: 0.7,
    anchor: { x: 1, y: 0, z: 2 }, base: { W: 0.5, H: 1.8, D: 0.6 },
    faces: [], cutouts: [], cfd: { role: 'wall' } };
  const m = V.finalizeNewModelV4(boxSeed);
  // uid + V4 geometry shape
  assert.equal(m.uid, boxSeed.id);
  assert.deepEqual(m.geometry.location, { x: 1, y: 0, z: 2 });
  assert.deepEqual(m.geometry.size, { x: 0.5, y: 1.8, z: 0.6 });
  // non-enumerable working kind, NO alias props
  assert.equal(m.kind, 'box');
  assert.equal(Object.prototype.propertyIsEnumerable.call(m, 'kind'), false);
  assert.ok(!('anchor' in m) && !('base' in m) && !('cfd' in m) && !('id' in m));
  // clean serialize (working kind non-enumerable)
  const j = JSON.parse(JSON.stringify(m));
  assert.ok(!('anchor' in j) && !('base' in j) && !('cfd' in j) && !('id' in j) && !('kind' in j));
});

test('finalizeNewModelV4: box-subtype seed keeps v3 kind as working discriminator', () => {
  // heat_source migrates into the boxes bucket; the working kind must stay 'heat_source'
  // (the v3 seed kind), matching what normalizeStateV4 restores from meta.legacyKind.
  const hsSeed = { id: 'hs_f1', kind: 'heat_source', name: 'H', enabled: true,
    anchor: { x: 0, y: 0, z: 0 }, base: { W: 1, H: 1, D: 1 },
    heatSource: { heatMode: 'power', totalPower: 500 }, cfd: { role: 'heatSource' } };
  const m = V.finalizeNewModelV4(hsSeed);
  assert.equal(m.kind, 'heat_source');
  assert.equal(Object.prototype.propertyIsEnumerable.call(m, 'kind'), false);
  assert.equal(m.meta.legacyKind, 'heat_source');
});

test('finalizeNewModelV4: custom seed -> customMesh fields (no legacy aliases)', () => {
  const customSeed = { id:'m_c', kind:'custom', name:'c', anchor:{x:0,y:0,z:0}, base:{W:1,H:1,D:1},
    customSTLBase64:'BASE64DATA', customOffset:{x:1,y:2,z:3}, solidRegions:[{name:'r1'}], cfd:{} };
  const m = V.finalizeNewModelV4(customSeed);
  // data lives V4-native under customMesh (no solidRegions/customSTLBase64/customOffset aliases)
  assert.ok(!('solidRegions' in m) && !('customSTLBase64' in m) && !('customOffset' in m));
  assert.ok(Array.isArray(m.customMesh.solidRegions));
  assert.equal(m.customMesh.solidRegions.length, 1);
  assert.equal(m.customMesh.stlBase64, 'BASE64DATA');
  assert.deepEqual(m.customMesh.offset, { x:1, y:2, z:3 });
  // clean serialize: data lives only under customMesh
  const j = JSON.parse(JSON.stringify(m));
  assert.ok(!('solidRegions' in j) && !('customSTLBase64' in j) && !('customOffset' in j));
  assert.ok(j.customMesh && Array.isArray(j.customMesh.solidRegions));
});

test('finalizeNewModelV4: rack seed -> servers bucket (V4-native, no equipments alias)', () => {
  const rackSeed = { id: 'rk_f1', kind: 'rack', name: 'R', enabled: true,
    anchor: { x: 0, y: 0, z: 0 }, base: { W: 0.6, H: 2.0, D: 1.2 }, faces: [],
    rack: { unitCount: 42, coolingType: 'air',
            equipments: [{ id: 'e1', type: 'fanServer', startUnit: 1, spanUnits: 2 }] },
    cfd: { role: 'rack' } };
  const m = V.finalizeNewModelV4(rackSeed);
  assert.equal(m.kind, 'rack');
  assert.ok(!('equipments' in m));
  assert.equal(Object.keys(m.constructions.servers).length, 1);
  assert.ok('e1' in m.constructions.servers);
});
