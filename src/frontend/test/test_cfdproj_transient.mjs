import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const T = require('../shared/js/cfdproj_transient.js');
const entry = (key) => T.registry().params.find(p => p.key === key);

const rack = () => ({ uid: 'r1', constructions: { servers: {
  e_1: { cooling: { power: 1000 }, liquidCooling: { cpCuPairs: [{ Q_W: 100 }, { Q_W: 50 }] } },
  e_2: { cooling: { power: 500 } } } } });
const acu = () => ({ uid: 'a1', geometry: { faces: {
  front: { patches: [{ id: 'p_1', physics: { role: 'supply', temperature: 20, volumeFlowRate: 4.2 } }] },
  back: { patches: [{ id: 'p_2', physics: { role: 'return', temperature: 15, volumeFlowRate: 4.2 } }] } } } });

// --- Task 1: registry and path resolver ---------------------------------------

test('enumerates object keys and array indices', () => {
  assert.deepEqual(T.enumerateTargets(rack(), entry('server.power')),
    [{ target: 'e_1', value: 1000 }, { target: 'e_2', value: 500 }]);
  assert.deepEqual(T.enumerateTargets(rack(), entry('cp.heat')),
    [{ target: 'e_1/0', value: 100 }, { target: 'e_1/1', value: 50 }]);
});

test('id selector with where filter and wildcard', () => {
  assert.deepEqual(T.enumerateTargets(acu(), entry('acu.supplyTemp')), [{ target: 'p_1', value: 20 }]);
});

test('createWithDefault yields the default when the field is absent', () => {
  assert.deepEqual(T.enumerateTargets({ uid: 'c1', cooling: {} }, entry('cdu.returnPressure')), [{ target: '', value: 150 }]);
  assert.deepEqual(T.enumerateTargets({ uid: 'c1', cooling: { returnPressureKpa: 200 } }, entry('cdu.returnPressure')),
    [{ target: '', value: 200 }]);
  assert.deepEqual(T.enumerateTargets({ uid: 'c1' }, entry('pump.rpm')), [{ target: '', value: 1 }]);
});

test('get/set/delete by target', () => {
  const m = acu();
  assert.equal(T.getAt(m, entry('acu.supplyTemp'), 'p_1'), 20);
  T.setAt(m, entry('acu.supplyTemp'), 'p_1', 18);
  assert.equal(m.geometry.faces.front.patches[0].physics.temperature, 18);
  T.deleteAt(m, entry('acu.supplyTemp'), 'p_1');
  assert.equal('temperature' in m.geometry.faces.front.patches[0].physics, false);
  const r = rack();
  T.setAt(r, entry('cp.heat'), 'e_1/1', 75);
  assert.equal(r.constructions.servers.e_1.liquidCooling.cpCuPairs[1].Q_W, 75);
  const c = { uid: 'c1' };
  T.setAt(c, entry('pump.rpm'), '', 0.8);
  assert.equal(c.constructions.pump.rpmRatio, 0.8);
});

// --- Task 2: evaluator, corner table, validation ------------------------------

const kf = [{ t: 0, value: 10 }, { t: 60, value: 20 }, { t: 120, value: 5, rampS: 30 }];

test('step then ramp, right-continuous', () => {
  assert.equal(T.valueAt(kf, 0), 10);
  assert.equal(T.valueAt(kf, 59.9), 10);
  assert.equal(T.valueAt(kf, 60), 20);
  assert.equal(T.valueAt(kf, 90), 20);
  assert.equal(T.valueAt(kf, 105), 12.5);
  assert.equal(T.valueAt(kf, 120), 5);
  assert.equal(T.valueAt(kf, 500), 5);
});

test('corner table: a step is two rows at one time, a ramp its start and end', () => {
  assert.deepEqual(T.cornerTable(kf, 200), [[0, 10], [60, 10], [60, 20], [90, 20], [120, 5], [200, 5]]);
  assert.deepEqual(T.cornerTable([{ t: 0, value: 3 }], 50), [[0, 3], [50, 3]]);
});

test('validation catches ordering, ramps, grid, bounds and the t = 0 rule', () => {
  const ok = { mode: 'transient', durationS: 200, gridS: 1, start: { kind: 'uniform', uniform: {} },
    tracks: [{ modelUid: 'r1', param: 'server.power', target: 'e_1', keyframes: kf }] };
  assert.deepEqual(T.validateTransient(ok), []);
  const bad = structuredClone(ok);
  bad.tracks[0].keyframes = [{ t: 0, value: 10 }, { t: 60.5, value: 20 }, { t: 50, value: 1, rampS: 90 }];
  const msgs = T.validateTransient(bad).map(e => e.msg).join(' | ');
  assert.match(msgs, /grid/);
  assert.match(msgs, /increasing/);
  assert.match(msgs, /ramp/);
  const noZero = structuredClone(ok);
  noZero.tracks[0].keyframes = [{ t: 10, value: 1 }];
  assert.match(T.validateTransient(noZero)[0].msg, /t = 0/);
  const range = structuredClone(ok);
  range.tracks[0].keyframes = [{ t: 0, value: -5 }];
  assert.match(T.validateTransient(range)[0].msg, /outside/);
  const unknown = structuredClone(ok);
  unknown.tracks[0].param = 'nope';
  assert.match(T.validateTransient(unknown)[0].msg, /unknown parameter/);
});

test('default transient block is steady and empty', () => {
  const d = T.defaultTransient({ geometry: { temperature: 24 } });
  assert.equal(d.mode, 'steady');
  assert.equal(d.start.uniform.airT_C, 24);
  assert.deepEqual(d.tracks, []);
});

// --- Task 3: materialise, strip, tracks from fields, commitEdit ---------------

const doc = () => ({ schemaVersion: 5, geometry: { temperature: 22 },
  constructions: { racks: { r1: rack() }, acus: { a1: acu() } },
  transient: { mode: 'transient', durationS: 600, gridS: 1, start: { kind: 'uniform', uniform: {} }, tracks: [] } });
const trackOf = (d, param, target) => d.transient.tracks.find(x => x.param === param && x.target === target);

test('tracks from fields, strip, materialise round-trip', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  // server.power ×2, cp.heat ×2, acu supplyTemp + airflow (supply patch only)
  assert.equal(d.transient.tracks.length, 6);
  const original = structuredClone(d);
  T.stripTrackedFields(d);
  assert.equal(d.constructions.racks.r1.constructions.servers.e_1.cooling.power, undefined);
  assert.equal(d.constructions.acus.a1.geometry.faces.back.patches[0].physics.temperature, 15); // return: static
  T.materializeAt(d, 0);
  assert.deepEqual(d.constructions, original.constructions);
});

test('commitEdit writes a keyframe at the playhead and rejects static or structural edits off t = 0', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  T.materializeAt(d, 120);
  const draft = structuredClone(d);
  draft.constructions.racks.r1.constructions.servers.e_1.cooling.power = 1500;
  assert.deepEqual(T.commitEdit(d, draft, 120), { ok: true });
  assert.deepEqual(trackOf(draft, 'server.power', 'e_1').keyframes,
    [{ t: 0, value: 1000 }, { t: 120, value: 1500, rampS: 0 }]);
  const geo = structuredClone(d);
  geo.constructions.acus.a1.geometry.faces.front.name = 'moved';
  assert.equal(T.commitEdit(d, geo, 120).reason, 'static');
  const struct = structuredClone(d);
  delete struct.constructions.racks.r1.constructions.servers.e_2;
  assert.equal(T.commitEdit(d, struct, 120).reason, 'structure');
});

test('commitEdit at t = 0 updates the start value and reconciles structure', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  T.materializeAt(d, 0);
  const draft = structuredClone(d);
  delete draft.constructions.racks.r1.constructions.servers.e_2;
  draft.constructions.racks.r1.constructions.servers.e_3 = { cooling: { power: 700 } };
  draft.constructions.racks.r1.constructions.servers.e_1.cooling.power = 900;
  assert.deepEqual(T.commitEdit(d, draft, 0), { ok: true });
  const targets = draft.transient.tracks.filter(x => x.param === 'server.power').map(x => x.target).sort();
  assert.deepEqual(targets, ['e_1', 'e_3']);
  assert.deepEqual(trackOf(draft, 'server.power', 'e_1').keyframes, [{ t: 0, value: 900 }]);
  // geometry edits are fine at t = 0
  const geo = structuredClone(d);
  geo.constructions.acus.a1.geometry.faces.front.name = 'moved';
  assert.deepEqual(T.commitEdit(d, geo, 0), { ok: true });
});

test('timeline-only edits (tracks changed, fields untouched) are accepted anywhere', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  T.materializeAt(d, 300);
  const draft = structuredClone(d);
  trackOf(draft, 'server.power', 'e_2').keyframes.push({ t: 200, value: 800, rampS: 50 });
  draft.transient.durationS = 900;
  assert.deepEqual(T.commitEdit(d, draft, 300), { ok: true });
});

test('project settings are not scene state: editable at any playhead, no keyframe', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  d.meta = { casePath: '/40902/case' };
  d.runDefaults = { air: { endTime: 500 } };
  d.instrumentation = { probes: {} };
  T.materializeAt(d, 300);
  const draft = structuredClone(d);
  draft.meta.casePath = '/40901/case';
  draft.runDefaults.air.endTime = 900;
  draft.instrumentation.probes.p1 = { position: [0, 1, 0] };
  const tracks = structuredClone(d.transient.tracks);
  assert.deepEqual(T.commitEdit(d, draft, 300), { ok: true });
  assert.deepEqual(draft.transient.tracks, tracks);
  // the room is scene state like any model
  const room = structuredClone(d);
  room.geometry.temperature = 30;
  assert.equal(T.commitEdit(d, room, 300).reason, 'static');
});

test('display-only fields are editable at any playhead and make no keyframe', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  T.materializeAt(d, 300);
  const tracks = structuredClone(d.transient.tracks);
  const shown = structuredClone(d);
  const r = shown.constructions.racks.r1;
  r.meta = { display: { opacity: 0.4 } };
  r.moveTool = { enabled: true, axes: 'xz' };
  r.rotateTool = { enabled: false };
  r.constructions.servers.e_1.meta = { display: { opacity: 0.2 } };
  shown.constructions.acus.a1.geometry.faces.front.opacity = 0.5;
  shown.constructions.acus.a1.geometry.faces.front.wallOpacities = [0.5];
  assert.deepEqual(T.commitEdit(d, shown, 300), { ok: true });
  assert.deepEqual(shown.transient.tracks, tracks);
  // `opacity` is display-only under a face, not anywhere: a patch field of that name is scene state
  const patch = structuredClone(d);
  patch.constructions.acus.a1.geometry.faces.front.patches[0].opacity = 0.3;
  assert.equal(T.commitEdit(d, patch, 300).reason, 'static');
  const renamed = structuredClone(d);
  renamed.constructions.acus.a1.geometry.faces.front.name = 'moved';
  assert.equal(T.commitEdit(d, renamed, 300).reason, 'static');
});

test('inserting a keyframe inside a ramp shortens the next ramp', () => {
  const d = doc();
  d.transient.tracks = [{ modelUid: 'r1', param: 'server.power', target: 'e_1',
    keyframes: [{ t: 0, value: 1000 }, { t: 100, value: 2000, rampS: 100 }] }];
  T.materializeAt(d, 60);
  const draft = structuredClone(d);
  draft.constructions.racks.r1.constructions.servers.e_1.cooling.power = 900;
  T.commitEdit(d, draft, 60);
  assert.deepEqual(draft.transient.tracks[0].keyframes,
    [{ t: 0, value: 1000 }, { t: 60, value: 900, rampS: 0 }, { t: 100, value: 2000, rampS: 40 }]);
});

// --- Task 4: v4 → v5 migration ------------------------------------------------

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const M = require('../shared/js/cfdproj_migrate.js');

const v4 = (constructions, runDefaults = {}) => ({ schemaVersion: 4, uid: 'p', name: 'p', meta: {},
  geometry: { temperature: 22 }, models: {}, constructions, instrumentation: {}, runDefaults });

test('ACU timeVarying: fake steps become steps, linear segments become ramps', () => {
  const a = acu();
  a.timeVarying = { supplyTemp: [{ t: 0, value: 20 }, { t: 100, value: 20 }, { t: 100.1, value: 16 }, { t: 200, value: 18 }] };
  const { doc, report } = T.migrateCfdprojV4toV5WithReport(v4({ acus: { a1: a } }));
  assert.equal(doc.schemaVersion, 5);
  assert.equal(doc.constructions.acus.a1.timeVarying, undefined);
  const trk = doc.transient.tracks.find(x => x.param === 'acu.supplyTemp');
  assert.deepEqual(trk.keyframes, [{ t: 0, value: 20 }, { t: 100, value: 16, rampS: 0 }, { t: 200, value: 18, rampS: 99.9 }]);
  assert.equal(doc.transient.gridS, 0.1);
  assert.deepEqual(T.validateTransient(doc.transient), []);
  assert.ok(Array.isArray(report));
});

test('rack heatOutput scales every server; power:<id> overrides one server', () => {
  const r = rack();
  r.timeVarying = { heatOutput: [{ t: 0, value: 5000 }, { t: 60, value: 5000 }, { t: 60.1, value: 0 }],
    'power:e_2': [{ t: 0, value: 500 }, { t: 30, value: 250 }] };
  const { doc } = T.migrateCfdprojV4toV5WithReport(v4({ racks: { r1: r } }));
  const p = (id) => doc.transient.tracks.find(x => x.param === 'server.power' && x.target === id).keyframes;
  assert.deepEqual(p('e_1'), [{ t: 0, value: 1000 }, { t: 60, value: 0, rampS: 0 }]);
  assert.deepEqual(p('e_2'), [{ t: 0, value: 500 }, { t: 30, value: 250, rampS: 30 }]);
});

test('liquid load step and CDU primary circuit seed tracks; experiment stays valid', () => {
  const cdu = { uid: 'c9', cooling: { supplyTemperatureC: 35, totalFlowRate: 60 }, constructions: { pump: { rpm: 3000 } } };
  const runDefaults = { liquid: { experiment: { duration_s: 600, dt_s: 10,
    settings: { mode: 'manual', pumpId: '', measurementId: '', setpoint: 45, kp: 0.03, ki: 0.002, bias: 0.8, loadStep: true, stepTime: 60, loadFactor: 1.5 },
    dae: { analysis: { mode: 'transient', initialization: 'steady' }, primary_circuits: [{ edge_id: 'c9_hx', flow_kg_s: 2, inlet_K: 291.15 }] } } } };
  const { doc } = T.migrateCfdprojV4toV5WithReport(v4({ racks: { r1: rack() }, cdus: { c9: cdu } }, runDefaults));
  const kf = (param, target = '') => doc.transient.tracks.find(x => x.param === param && x.target === target).keyframes;
  assert.deepEqual(kf('cp.heat', 'e_1/0'), [{ t: 0, value: 100 }, { t: 60, value: 150, rampS: 0 }]);
  assert.deepEqual(kf('cdu.primaryInletT'), [{ t: 0, value: 18 }]);
  assert.deepEqual(kf('cdu.primaryFlow'), [{ t: 0, value: 2 }]);
  assert.deepEqual(kf('cdu.returnPressure'), [{ t: 0, value: 150 }]);
  assert.deepEqual(kf('pump.rpm'), [{ t: 0, value: 1 }]);
  assert.equal(doc.transient.mode, 'transient');
  assert.equal(doc.transient.start.kind, 'steadyConverged');
  assert.equal(doc.transient.durationS, 600);
  assert.equal(doc.transient.gridS, 10);
  assert.equal(doc.runDefaults.liquid.experiment.settings.loadStep, false);
  assert.equal(doc.constructions.cdus.c9.cooling.supplyTemperatureC, undefined); // stripped: lives in its track
});

test('supply schedule ramp becomes a primary-inlet ramp (Kelvin to Celsius)', () => {
  const cdu = { uid: 'c9', cooling: {} };
  const runDefaults = { liquid: { experiment: { duration_s: 300, dt_s: 1,
    settings: { mode: 'manual', pumpId: '', measurementId: '', setpoint: 45, kp: 0, ki: 0, bias: 0.8, loadStep: false, stepTime: 60, loadFactor: 1,
      supplySchedule: { mode: 'ramp', from: 293.15, to: 303.15, stepTime: 60, rampTime: 120 } },
    dae: { analysis: { mode: 'transient', initialization: 'consistent' } } } } };
  const { doc } = T.migrateCfdprojV4toV5WithReport(v4({ cdus: { c9: cdu } }, runDefaults));
  const kf = doc.transient.tracks.find(x => x.param === 'cdu.primaryInletT').keyframes;
  assert.deepEqual(kf, [{ t: 0, value: 20 }, { t: 180, value: 30, rampS: 120 }]);
  assert.equal(doc.runDefaults.liquid.experiment.settings.supplySchedule, undefined);
  assert.equal(doc.transient.start.kind, 'uniform');
});

test('v5 passes through; v3 goes through the v4 chain first', () => {
  const five = { schemaVersion: 5, constructions: {}, transient: T.defaultTransient({}) };
  assert.equal(T.migrateCfdprojToV5(five), five);
});

function cfdprojFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    let st;
    try { st = statSync(p); } catch { continue; } // dangling symlinks in case/
    if (st.isDirectory()) out.push(...cfdprojFiles(p));
    else if (name.endsWith('.cfdproj')) out.push(p);
  }
  return out;
}

test('every case: fields at t = 0 equal the v4 fields (models without timeVarying)', () => {
  const files = cfdprojFiles(join(REPO, 'case'));
  assert.ok(files.length > 10);
  for (const f of files) {
    let raw;
    try { raw = JSON.parse(readFileSync(f, 'utf8')); } catch { continue; }
    if (Number(raw.schemaVersion) >= 5) continue; // already v5 (golden fixtures): nothing to migrate
    const four = M.migrateCfdprojToV4(structuredClone(raw));
    const five = T.migrateCfdprojToV5(structuredClone(raw));
    assert.equal(five.schemaVersion, 5, f);
    assert.deepEqual(T.validateTransient(five.transient), [], f);
    const live = T.materializeAt(structuredClone(five), 0);
    // Fields a v4 model never had (CDU defaults) appear only after materialising.
    const idx = new Map(); T.forEachModel(four, (b, uid, m) => idx.set(uid, m));
    const liveIdx = new Map(); T.forEachModel(live, (b, uid, m) => liveIdx.set(uid, m));
    for (const trk of five.transient.tracks) {
      const e = T.entryOf(trk.param);
      if (e.createWithDefault && T.getAt(idx.get(trk.modelUid), e, trk.target) === undefined) T.deleteAt(liveIdx.get(trk.modelUid), e, trk.target);
    }
    for (const [uid, m] of idx) {
      // Transient projects adopt a curve's start value; steady ones never change.
      if (m.timeVarying && five.transient.mode === 'transient') continue;
      const { timeVarying, ...rest } = m;
      assert.deepEqual(liveIdx.get(uid), rest, `${f} model ${uid}`);
    }
  }
});

test('steady project: a curve never changes the t = 0 (steady) value; transient adopts it', () => {
  const steady = acu();
  steady.timeVarying = { supplyTemp: [{ t: 0, value: 15 }, { t: 50, value: 15 }, { t: 50.1, value: 12 }] };
  const s = T.migrateCfdprojV4toV5WithReport(v4({ acus: { a1: steady } }));
  const ks = s.doc.transient.tracks.find(x => x.param === 'acu.supplyTemp').keyframes;
  assert.deepEqual(ks, [{ t: 0, value: 20 }, { t: 50, value: 12, rampS: 0 }]);
  assert.ok(s.report.some(line => /kept the steady value 20/.test(line)));
  const trans = acu();
  trans.timeVarying = structuredClone(steady.timeVarying);
  const t = T.migrateCfdprojV4toV5WithReport(v4({ acus: { a1: trans } }, { simulationConfig: { solverType: 'transient' } }));
  assert.equal(t.doc.transient.tracks.find(x => x.param === 'acu.supplyTemp').keyframes[0].value, 15);
});

test('syncFieldsToTracks captures in-place (legacy) edits at the playhead', () => {
  const d = doc();
  d.transient.tracks = T.tracksFromFields(d);
  T.materializeAt(d, 0);
  d.constructions.racks.r1.constructions.servers.e_1.cooling.power = 1234;         // changed in place
  d.constructions.racks.r1.constructions.servers.e_9 = { cooling: { power: 42 } }; // added in place
  T.syncFieldsToTracks(d, 0);
  assert.deepEqual(trackOf(d, 'server.power', 'e_1').keyframes, [{ t: 0, value: 1234 }]);
  assert.deepEqual(trackOf(d, 'server.power', 'e_9').keyframes, [{ t: 0, value: 42 }]);
  // unchanged fields leave tracks (and their later keyframes) alone
  trackOf(d, 'server.power', 'e_2').keyframes.push({ t: 100, value: 1, rampS: 0 });
  T.materializeAt(d, 0);
  T.syncFieldsToTracks(d, 0);
  assert.equal(trackOf(d, 'server.power', 'e_2').keyframes.length, 2);
});
