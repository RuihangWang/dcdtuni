import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

global.window = new JSDOM('').window;
global.document = global.window.document;

await import('../shared/js/cfdproj_migrate.js');
const { migrateCfdprojV2toV3, CFDPROJ_SCHEMA_VERSION } = window;

test('schema version constant is 3', () => {
  assert.equal(CFDPROJ_SCHEMA_VERSION, 3);
});

test('idempotent: a v3 doc passes through unchanged', () => {
  const v3 = { schemaVersion: 3, meta: { projectName: 'X' }, model: { racks: {}, acus: {} },
    construction: [], probeData: [], sensorData: [], simulation: {}, ui: {} };
  const out = migrateCfdprojV2toV3(v3);
  assert.deepEqual(out, v3);
});

const V2 = {
  schemaVersion: 2,
  projectName: 'Base_leakage', exportFileName: 'default', inputUnit: 'm',
  bounds: { x: { min: 0, max: 4.4 } },
  roomPolygon: { vertices: [[0, 0], [4, 0], [4, 6], [0, 6]], height: 3.6 },
  models: [ { id: 'm_1', kind: 'rack', name: 'R1' }, { id: 'm_2', kind: 'acu', name: 'A1' } ],
  sensorPoints: [ { id: 's1', name: 's1', x: 1, y: 2, z: 3, enabled: true } ],
  cfdRoom: { height: 3.6, roomTemperature: 22, meshBaseSize: 0.34, solverType: 'steady' },
  dctwinConfig: { casePath: '/abs/case', activeJobId: 'j1', meshReady: true },
  displayMode: 'air', snapFace: true, selection: { type: 'project' }, groups: [],
  previewVisibility: {}, treeCollapsed: {},
  rackDetailTarget: null, rackDetailReturnMode: '3d', rackDetailShowContext: false,
  liquidCooling: { enabled: false },
};

test('top-level sections exist with correct nesting', () => {
  const v3 = migrateCfdprojV2toV3(V2);
  assert.equal(v3.schemaVersion, 3);
  assert.equal(v3.meta.projectName, 'Base_leakage');
  assert.equal(v3.meta.inputUnit, 'm');
  assert.equal(v3.meta.description, '');
  assert.deepEqual(v3.meta.room.bounds, V2.bounds);
  assert.deepEqual(v3.meta.room.polygon, V2.roomPolygon);
  assert.equal(v3.meta.room.height, 3.6);
  assert.equal(v3.meta.room.temperature, 22);
  assert.equal(v3.meta.case.path, '/abs/case');
});

test('models -> construction with fromModel; library empty; probeData/sensorData', () => {
  const v3 = migrateCfdprojV2toV3(V2);
  assert.equal(v3.construction.length, 2);
  assert.equal(v3.construction[0].fromModel, null);
  assert.equal(v3.construction[0].kind, 'rack');
  assert.deepEqual(v3.model, { racks: {}, acus: {} });
  assert.equal(v3.probeData.length, 1);
  assert.deepEqual(v3.sensorData, []);
});

test('simulation = cfdRoom minus room-geometry keys', () => {
  const v3 = migrateCfdprojV2toV3(V2);
  assert.equal(v3.simulation.meshBaseSize, 0.34);
  assert.equal(v3.simulation.solverType, 'steady');
  assert.ok(!('height' in v3.simulation));
  assert.ok(!('roomTemperature' in v3.simulation));
});

test('ui gathers editor/session; casePath stripped from session', () => {
  const v3 = migrateCfdprojV2toV3(V2);
  assert.equal(v3.ui.displayMode, 'air');
  assert.equal(v3.ui.snapFace, true);
  assert.deepEqual(v3.ui.selection, { type: 'project' });
  assert.equal(v3.ui.rackDetail.returnMode, '3d');
  assert.equal(v3.ui.session.activeJobId, 'j1');
  assert.ok(!('casePath' in v3.ui.session));
  assert.equal(v3.liquidCooling.enabled, false);
});

test('absent schemaVersion is treated as legacy and migrated', () => {
  const noVer = { ...V2 }; delete noVer.schemaVersion;
  const v3 = migrateCfdprojV2toV3(noVer);
  assert.equal(v3.schemaVersion, 3);
  assert.equal(v3.construction.length, 2);
});

test('missing optional keys default safely', () => {
  const sparse = { models: [], cfdRoom: { height: 3 } }; // no sensorPoints, no bounds
  const v3 = migrateCfdprojV2toV3(sparse);
  assert.deepEqual(v3.probeData, []);
  assert.equal(v3.meta.room.polygon, null); // absent roomPolygon -> null
  assert.equal(v3.meta.room.temperature, 22); // default
});

test('does not mutate the input object (Phase 5 batch conversion relies on this)', () => {
  const src = JSON.parse(JSON.stringify(V2));
  migrateCfdprojV2toV3(src);
  assert.ok('height' in src.cfdRoom, 'input cfdRoom.height must be preserved');
  assert.ok('roomTemperature' in src.cfdRoom, 'input cfdRoom.roomTemperature must be preserved');
  assert.ok('casePath' in src.dctwinConfig, 'input dctwinConfig.casePath must be preserved');
});

test('v3 passthrough returns the same object reference (no needless copy)', () => {
  const v3 = { schemaVersion: 3, meta: {}, model: { racks: {}, acus: {} },
    construction: [], probeData: [], sensorData: [], simulation: {}, ui: {} };
  assert.strictEqual(migrateCfdprojV2toV3(v3), v3);
});
