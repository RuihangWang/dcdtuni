import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const S = require('../shared/js/cfdproj_schema.js');

test('schema version and bucket registry', () => {
  assert.equal(S.SCHEMA_VERSION, 4);
  assert.ok(S.BUCKETS.includes('racks'));
  assert.equal(S.BUCKET_BY_KIND.rack, 'racks');
  assert.equal(S.BUCKET_BY_KIND.pipe_segment, 'pipeSegments');
  assert.equal(S.BUCKET_BY_KIND.custom, 'customMeshes');
  assert.equal(S.BUCKET_BY_KIND.heat_source, 'boxes'); // legacy kind retired into boxes
});

test('emptyDocument validates clean', () => {
  const doc = S.emptyDocument();
  doc.uid = 'proj_test';
  assert.deepEqual(S.validateDocument(doc), []);
});

test('validateDocument flags structural problems', () => {
  const doc = S.emptyDocument();
  doc.uid = 'proj_test';
  doc.constructions.racks = [];                       // array, not dict
  let errs = S.validateDocument(doc);
  assert.ok(errs.some(e => e.path === '$.constructions.racks'));

  const doc2 = S.emptyDocument();
  doc2.uid = 'proj_test';
  doc2.constructions.racks = { r1: { uid: 'MISMATCH', geometry: {} } };
  errs = S.validateDocument(doc2);
  assert.ok(errs.some(e => e.path.includes('racks.r1')));

  assert.ok(S.validateDocument({ schemaVersion: 3 }).length > 0);
});

test('mesh/sim key lists are disjoint', () => {
  const overlap = S.MESH_KEYS.filter(k => S.SIM_KEYS.includes(k));
  assert.deepEqual(overlap, []);
});

function hybridHeatBudgetDocument() {
  const doc = S.emptyDocument();
  doc.uid = 'hybrid-project';
  doc.constructions.racks.rack_1 = {
    uid: 'rack_1', geometry: {},
    constructions: { servers: { server_1: { uid: 'server_1', geometry: {} } } },
  };
  doc.runDefaults.hybridHeatBudget = {
    version: 1,
    source: 'mock',
    source_reference: 'Synthetic Shinaide heat-budget fixture; not telemetry',
    load_mapping: 'shared_heat_multiplier',
    air_surface_allocation: { front_fraction: 0.5, rear_fraction: 0.5 },
    servers: { 'rack_1/server_1': { total_it_power_W: 1500 } },
  };
  return doc;
}

test('optional hybrid heat budget accepts an explicit UID-bound total IT power declaration', () => {
  const doc = hybridHeatBudgetDocument();
  assert.deepEqual(S.validateDocument(doc), []);
  assert.equal(S.emptyDocument().runDefaults.hybridHeatBudget, undefined);
});

test('hybrid heat budget requires a closed versioned source and load mapping', () => {
  const mutations = [
    ['version', value => { value.version = 2; }],
    ['source', value => { value.source = 'estimated'; }],
    ['source_reference', value => { value.source_reference = '   '; }],
    ['load_mapping', value => { value.load_mapping = 'independent'; }],
    ['unknown', value => { value.unexpected = true; }],
    ['missing', value => { delete value.servers; }],
  ];
  for (const [label, mutate] of mutations) {
    const doc = hybridHeatBudgetDocument();
    mutate(doc.runDefaults.hybridHeatBudget);
    assert.ok(S.validateDocument(doc).some(e => e.path.startsWith('$.runDefaults.hybridHeatBudget')), label);
  }
  for (const bad of [null, [], 'not an object']) {
    const doc = hybridHeatBudgetDocument();
    doc.runDefaults.hybridHeatBudget = bad;
    assert.ok(S.validateDocument(doc).some(e => e.path === '$.runDefaults.hybridHeatBudget'));
  }
});

test('hybrid heat budget rejects incomplete and nonphysical per-server watt values', () => {
  for (const watts of [undefined, null, '1500', 0, -1, NaN, Infinity, -Infinity]) {
    const doc = hybridHeatBudgetDocument();
    doc.runDefaults.hybridHeatBudget.servers['rack_1/server_1'].total_it_power_W = watts;
    assert.ok(
      S.validateDocument(doc).some(e => e.path.endsWith('rack_1/server_1.total_it_power_W')),
      String(watts),
    );
  }
  for (const invalid of [{}, [], null]) {
    const doc = hybridHeatBudgetDocument();
    doc.runDefaults.hybridHeatBudget.servers = invalid;
    assert.ok(S.validateDocument(doc).some(e => e.path === '$.runDefaults.hybridHeatBudget.servers'));
  }
  const doc = hybridHeatBudgetDocument();
  doc.runDefaults.hybridHeatBudget.servers['rack_1/server_1'].extra = 1;
  assert.ok(S.validateDocument(doc).some(e => e.path.endsWith('rack_1/server_1.extra')));
});

test('hybrid heat budget keys resolve exactly to a rack and its nested stable server UID', () => {
  for (const invalidKey of ['rack_1/missing', 'missing/server_1', 'rack_1/Server Name', 'rack_1/server_1/extra', 'rack_1', '/server_1']) {
    const doc = hybridHeatBudgetDocument();
    doc.runDefaults.hybridHeatBudget.servers[invalidKey] = doc.runDefaults.hybridHeatBudget.servers['rack_1/server_1'];
    delete doc.runDefaults.hybridHeatBudget.servers['rack_1/server_1'];
    assert.ok(S.validateDocument(doc).some(e => e.path.includes(`.servers.${invalidKey}`)), invalidKey);
  }
  const doc = hybridHeatBudgetDocument();
  doc.constructions.racks.rack_1.constructions.servers.server_1.uid = 'renamed-server';
  assert.ok(S.validateDocument(doc).some(e => e.path.includes('.servers.rack_1/server_1')));
});

test('hybrid heat budget requires a finite nonnegative front/rear allocation summing to one', () => {
  const invalid = [
    [undefined, 'air_surface_allocation'],
    [null, 'air_surface_allocation'],
    [[], 'air_surface_allocation'],
    [{ front_fraction: 1 }, 'rear_fraction'],
    [{ rear_fraction: 1 }, 'front_fraction'],
    [{ front_fraction: -0.1, rear_fraction: 1.1 }, 'front_fraction'],
    [{ front_fraction: NaN, rear_fraction: 1 }, 'front_fraction'],
    [{ front_fraction: Infinity, rear_fraction: 0 }, 'front_fraction'],
    [{ front_fraction: '0.5', rear_fraction: 0.5 }, 'front_fraction'],
    [{ front_fraction: 0.4, rear_fraction: 0.5 }, 'air_surface_allocation'],
    [{ front_fraction: 0.5, rear_fraction: 0.5, side: 'front' }, 'side'],
  ];
  for (const [allocation, field] of invalid) {
    const doc = hybridHeatBudgetDocument();
    doc.runDefaults.hybridHeatBudget.air_surface_allocation = allocation;
    assert.ok(
      S.validateDocument(doc).some(e => e.path.includes(`.air_surface_allocation${field === 'air_surface_allocation' ? '' : '.' + field}`)),
      JSON.stringify(allocation),
    );
  }
  for (const allocation of [
    { front_fraction: 0, rear_fraction: 1 },
    { front_fraction: 1, rear_fraction: 0 },
    { front_fraction: 0.5000000000005, rear_fraction: 0.5 },
  ]) {
    const doc = hybridHeatBudgetDocument();
    doc.runDefaults.hybridHeatBudget.air_surface_allocation = allocation;
    assert.deepEqual(S.validateDocument(doc), [], JSON.stringify(allocation));
  }
});
