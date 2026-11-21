import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

global.window = new JSDOM('').window;
global.document = global.window.document;

await import('../shared/js/patch_selection.js');
const { createSelectionModel } = window;

function names(m) { return [...m.getAll()].sort(); }

test('plain click selects only that name', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b', 'c'] });
  assert.deepEqual(names(m), ['a']);
  assert.equal(m.getLastAnchor(), 'a');
});

test('ctrl toggles membership and updates anchor only on add', () => {
  const m = createSelectionModel({ onChange: () => {} });
  const ordered = ['a', 'b', 'c'];
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ordered });
  m.handleRowClick('b', { shift: false, ctrl: true,  orderedNames: ordered });
  assert.deepEqual(names(m), ['a', 'b']);
  assert.equal(m.getLastAnchor(), 'b');
  m.handleRowClick('a', { shift: false, ctrl: true,  orderedNames: ordered });
  assert.deepEqual(names(m), ['b']);
  assert.equal(m.getLastAnchor(), 'b'); // unchanged on remove
});

test('shift selects inclusive range from anchor, either direction', () => {
  const m = createSelectionModel({ onChange: () => {} });
  const ordered = ['a', 'b', 'c', 'd', 'e'];
  m.handleRowClick('b', { shift: false, ctrl: false, orderedNames: ordered });
  m.handleRowClick('d', { shift: true,  ctrl: false, orderedNames: ordered });
  assert.deepEqual(names(m), ['b', 'c', 'd']);

  m.handleRowClick('a', { shift: true,  ctrl: false, orderedNames: ordered });
  assert.deepEqual(names(m), ['a', 'b']); // anchor still 'b', reverse range
});

test('shift+ctrl merges range into existing selection', () => {
  const m = createSelectionModel({ onChange: () => {} });
  const ordered = ['a', 'b', 'c', 'd', 'e'];
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ordered });
  m.handleRowClick('c', { shift: false, ctrl: true,  orderedNames: ordered });
  m.handleRowClick('e', { shift: true,  ctrl: true,  orderedNames: ordered });
  assert.deepEqual(names(m), ['a', 'c', 'd', 'e']); // c..e merged with {a,c}
});

test('shift with null anchor degrades to single-select', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('c', { shift: true, ctrl: false, orderedNames: ['a', 'b', 'c'] });
  assert.deepEqual(names(m), ['c']);
});

test('shift with name not in orderedNames degrades to single-select', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b', 'c'] });
  m.handleRowClick('z', { shift: true,  ctrl: false, orderedNames: ['a', 'b', 'c'] });
  assert.deepEqual(names(m), ['z']);
});

test('checkbox plain click → single target', () => {
  const m = createSelectionModel({ onChange: () => {} });
  const out = m.handleCheckboxClick('a', { shift: false, ctrl: false, nextVisible: true });
  assert.deepEqual(out, { targets: ['a'], visible: true });
});

test('checkbox with modifier + non-empty selection → batch', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b'] });
  m.handleRowClick('b', { shift: false, ctrl: true,  orderedNames: ['a', 'b'] });
  const out = m.handleCheckboxClick('a', { shift: true, ctrl: false, nextVisible: false });
  assert.deepEqual(out.targets.sort(), ['a', 'b']);
  assert.equal(out.visible, false);
});

test('checkbox with modifier + empty selection → single (no silent no-op)', () => {
  const m = createSelectionModel({ onChange: () => {} });
  const out = m.handleCheckboxClick('a', { shift: true, ctrl: false, nextVisible: true });
  assert.deepEqual(out, { targets: ['a'], visible: true });
});

test('setSelection replaces and fires onChange with correct diff', () => {
  let lastPrev, lastCurr;
  const m = createSelectionModel({
    onChange: (prev, curr) => { lastPrev = new Set(prev); lastCurr = new Set(curr); },
  });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b'] });
  m.setSelection(['b', 'c']);
  assert.deepEqual([...lastPrev].sort(), ['a']);
  assert.deepEqual([...lastCurr].sort(), ['b', 'c']);
  assert.deepEqual(names(m), ['b', 'c']);
});

test('clear empties selection and anchor', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a'] });
  m.clear();
  assert.deepEqual(names(m), []);
  assert.equal(m.getLastAnchor(), null);
});

test('has() reports membership', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b'] });
  assert.equal(m.has('a'), true);
  assert.equal(m.has('b'), false);
});

test('clear() is a no-op when already empty (no onChange fired)', () => {
  let calls = 0;
  const m = createSelectionModel({ onChange: () => { calls++; } });
  m.clear();
  assert.equal(calls, 0);
});

test('plain click on already-selected single row does not fire onChange', () => {
  let calls = 0;
  const m = createSelectionModel({ onChange: () => { calls++; } });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b'] });
  assert.equal(calls, 1);
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b'] });
  assert.equal(calls, 1, 'second click on same single-selected row must not fire');
});

test('modifier-click checkbox on non-selected row unions click-target into targets', () => {
  const m = createSelectionModel({ onChange: () => {} });
  m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ['a', 'b', 'z'] });
  m.handleRowClick('b', { shift: false, ctrl: true,  orderedNames: ['a', 'b', 'z'] });
  const out = m.handleCheckboxClick('z', { shift: true, ctrl: false, nextVisible: true });
  assert.deepEqual(out.targets.sort(), ['a', 'b', 'z']);
  assert.equal(out.visible, true);
});
