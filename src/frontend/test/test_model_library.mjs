import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
global.window = new JSDOM('').window; global.document = global.window.document;
await import('../shared/js/model_library.js');
const { extractTemplateFromModel, instantiateTemplate } = window;

test('extractTemplate strips identity/placement, keeps spec', () => {
  const rack = { id: 'm_1', name: 'R1', kind: 'rack', anchor: { x: 1 }, transform: { quaternion: {} },
    moveTool: {}, rotateTool: {}, enabled: true, showAnchorMarker: true, fromModel: 't_old',
    base: { W: 0.6 }, faces: { front: {} }, rack: { unitCount: 42 }, cfd: { role: 'rack', servers: [] } };
  const tpl = extractTemplateFromModel(rack, 'My Rack');
  assert.equal(tpl.label, 'My Rack');
  assert.equal(tpl.kind, 'rack');
  assert.deepEqual(tpl.base, { W: 0.6 });
  assert.equal(tpl.rack.unitCount, 42);
  // every identity/placement field must be stripped (all 9 EXCLUDE keys)
  for (const k of ['id', 'name', 'anchor', 'transform', 'moveTool', 'rotateTool', 'enabled', 'showAnchorMarker', 'fromModel']) {
    assert.ok(!(k in tpl), `template must not contain '${k}'`);
  }
});

test('instantiate produces an independent copy with fromModel + fresh id', () => {
  const tpl = { label: 'My Rack', kind: 'rack', base: { W: 0.6 }, faces: {}, rack: { unitCount: 42 }, cfd: { role: 'rack' } };
  const inst = instantiateTemplate(tpl, 't_1', () => 'm_99');
  assert.equal(inst.id, 'm_99');
  assert.equal(inst.fromModel, 't_1');
  assert.equal(inst.kind, 'rack');
  inst.rack.unitCount = 10;
  assert.equal(tpl.rack.unitCount, 42);
});
