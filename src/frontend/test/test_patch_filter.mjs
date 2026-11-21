import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="c"></div></body></html>');
global.window = dom.window;
global.document = dom.window.document;

// Load module (CommonJS-style side effect onto window)
await import('../shared/js/patch_filter.js');
const { createFilterBar } = window;

function fresh() {
  document.body.innerHTML = '<div id="c"></div>';
  return document.getElementById('c');
}

test('empty input matches everything', () => {
  const bar = createFilterBar({ container: fresh(), onChange: () => {} });
  assert.equal(bar.predicate('wall'), true);
  assert.equal(bar.predicate('anything'), true);
});

test('substring case-insensitive by default', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('.filter-input').value = 'wall';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  assert.equal(bar.predicate('frontWall'), true);
  assert.equal(bar.predicate('FRONTWALL'), true);
  assert.equal(bar.predicate('floor'), false);
});

test('case-sensitive toggle', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('[data-mode="case"]').click();
  c.querySelector('.filter-input').value = 'Wall';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  assert.equal(bar.predicate('frontWall'), true);
  assert.equal(bar.predicate('frontwall'), false);
});

test('whole-word toggle', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('[data-mode="whole"]').click();
  c.querySelector('.filter-input').value = 'wall';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  assert.equal(bar.predicate('wall'), true);
  assert.equal(bar.predicate('Wall'), true); // case off
  assert.equal(bar.predicate('frontwall'), false);
});

test('whole-word + case-sensitive combined', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('[data-mode="whole"]').click();
  c.querySelector('[data-mode="case"]').click();
  c.querySelector('.filter-input').value = 'wall';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  assert.equal(bar.predicate('wall'), true);
  assert.equal(bar.predicate('Wall'), false);
});

test('regex mode', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('[data-mode="regex"]').click();
  c.querySelector('.filter-input').value = '^fan_';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  assert.equal(bar.predicate('fan_inlet'), true);
  assert.equal(bar.predicate('wall_fan_A'), false);
});

test('invalid regex → zero matches, input gets .invalid class', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('[data-mode="regex"]').click();
  const input = c.querySelector('.filter-input');
  input.value = '[';
  input.dispatchEvent(new window.Event('input'));
  assert.equal(bar.predicate('anything'), false);
  assert.equal(input.classList.contains('invalid'), true);
});

test('getState / setState round-trip', () => {
  const c = fresh();
  const bar = createFilterBar({ container: c, onChange: () => {} });
  c.querySelector('[data-mode="case"]').click();
  c.querySelector('.filter-input').value = 'pump';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  const saved = bar.getState();
  assert.deepEqual(saved, { text: 'pump', case: true, whole: false, regex: false });

  // Rebuild a fresh bar and restore
  const c2 = fresh();
  const bar2 = createFilterBar({ container: c2, onChange: () => {} });
  bar2.setState(saved);
  assert.equal(c2.querySelector('.filter-input').value, 'pump');
  assert.equal(c2.querySelector('[data-mode="case"]').classList.contains('active'), true);
  assert.equal(bar2.predicate('Pump_1'), false); // case-sensitive re-applied
});

test('onChange callback receives current predicate', () => {
  const c = fresh();
  const calls = [];
  const bar = createFilterBar({ container: c, onChange: (pred) => calls.push(pred) });
  c.querySelector('.filter-input').value = 'x';
  c.querySelector('.filter-input').dispatchEvent(new window.Event('input'));
  assert.equal(calls.length, 1);
  assert.equal(typeof calls[0], 'function');
});
