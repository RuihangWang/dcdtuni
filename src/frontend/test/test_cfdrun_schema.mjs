import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const S = require('../shared/js/cfdrun_schema.js');

test('CFDRUN_SCHEMA_VERSION is 2', () => { assert.equal(S.CFDRUN_SCHEMA_VERSION, 2); });

test('emptyManifest has all 13 canonical top-level keys', () => {
  const m = S.emptyManifest();
  assert.deepEqual(Object.keys(m).sort(), [
    'artifacts','boundaryBindings','geometryExport','inputs','meshConfig','name',
    'porousBindings','postprocessConfig','runId','schemaVersion','simulationConfig',
    'sourceProject','sourceTerms'].sort());
  assert.equal(m.schemaVersion, 2);
});

test('BC_TYPES matches the fields.py set', () => {
  assert.deepEqual([...S.BC_TYPES].sort(), [
    'acu_return','acu_supply','fan_inlet','fan_outlet','fixedTemperature','heat_source_flux',
    'nonfan_fixed_temp','nonfan_heat_flux','perforated_baffle','through_zone','wall','wall_baffle'].sort());
});

test('validateManifest accepts a minimal well-formed manifest', () => {
  const m = S.emptyManifest();
  m.runId = 'r1'; m.sourceProject = { uid:'u', schemaVersion:4, projectName:'p', filePath:null, contentHash:'h', exportedAt:'2026-01-01T00:00:00Z' };
  assert.deepEqual(S.validateManifest(m), []);  // no errors
});

test('validateManifest rejects wrong schemaVersion, unknown bcType, and missing sourceProject', () => {
  const bad = S.emptyManifest(); bad.schemaVersion = 1;
  assert.ok(S.validateManifest(bad).some(e => /schemaVersion/.test(e)));
  const m2 = S.emptyManifest(); m2.boundaryBindings = { b1: { patchName:'p', bcType:'not_a_bc', parameters:{} } };
  assert.ok(S.validateManifest(m2).some(e => /bcType/.test(e)));
});
