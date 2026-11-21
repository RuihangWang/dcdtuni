import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInContext } from 'node:vm';
import { JSDOM } from 'jsdom';

const html = readFileSync(new URL('../web/STL_generate.html', import.meta.url), 'utf8');
const between = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const treeCode = between('    function _buildGroupedPatchTree(', '    window._ppPopulateFieldDropdown =');
const helpers = between('    function applyFilterToDOM(', '    function setPatchHighlight(');
const patches = ['Rack_1_inlet', 'Rack_1_outlet', 'Rack_2_inlet', 'Room_wall'].map((name, i) => ({
  name, nTriangles: 12, defaultVisible: i !== 1,
}));

function harness() {
  const dom = new JSDOM('<div id="pp-tree"></div>', { runScripts: 'outside-only' });
  const { window } = dom;
  Object.assign(window, {
    _kindOrder: ['rack', 'wall'],
    _kindLabels: { rack: 'RACK', wall: 'WALL' },
    _kindColors: {},
    _patchGroupLabel: name => name,
    _groupPatchesByModelKind: list => ({
      rack: { Rack_1: list.slice(0, 2), Rack_2: list.slice(2, 3) },
      wall: { Room: list.slice(3) },
    }),
    t: key => key,
    ppHighlightPatch() {},
    ensureViewportClickHandler() {},
    requestRender() {},
    ppUpdatePatchVisibility() {},
    CSS: { escape: value => value },
  });
  const context = dom.getInternalVMContext();
  for (const name of ['patch_filter.js', 'patch_selection.js']) {
    runInContext(readFileSync(new URL(`../shared/js/${name}`, import.meta.url), 'utf8'), context);
  }
  runInContext('let ppFilterBar = null; let ppSelectionModel = null;' + helpers + treeCode, context);
  return {
    window,
    build: runDir => window._ppBuildTree(patches, runDir),
    heads: () => Array.from(window.document.querySelectorAll('[data-collapse-key]')),
    close: () => window.close(),
  };
}

test('HTML postprocess starts all levels folded and preserves patch visibility defaults', t => {
  const h = harness(); t.after(h.close);
  h.build('/case/Run-1');
  assert.equal(h.heads().length, 4);
  h.heads().forEach(head => assert.equal(head.nextElementSibling.style.display, 'none'));
  const checks = Array.from(h.window.document.querySelectorAll('.pp-patch-cb'));
  assert.deepEqual(checks.map(cb => cb.checked), [true, false, true, true]);
  h.heads()[0].querySelector('span').click();
  assert.equal(h.heads()[0].nextElementSibling.style.display, '');
  assert.equal(h.heads()[1].nextElementSibling.style.display, 'none');
});

test('HTML keeps manual expansion on same-run refresh and folds a different run', t => {
  const h = harness(); t.after(h.close);
  h.build('/case/Run-1');
  h.heads()[0].querySelector('span').click();
  h.heads()[1].querySelector('span').click();
  const filter = h.window.document.querySelector('.filter-input');
  filter.value = 'inlet';
  filter.dispatchEvent(new h.window.Event('input', { bubbles: true }));
  h.build('/case/Run-1');
  assert.equal(h.heads()[0].nextElementSibling.style.display, '');
  assert.equal(h.heads()[1].nextElementSibling.style.display, '');
  assert.equal(h.heads()[2].nextElementSibling.style.display, 'none');
  assert.equal(h.window.document.querySelector('.filter-input').value, 'inlet');
  h.build('/case/Run-2');
  h.heads().forEach(head => assert.equal(head.nextElementSibling.style.display, 'none'));
});

test('HTML folded groups still apply checkbox gestures only to patches matching the filter', t => {
  const h = harness(); t.after(h.close);
  h.build('/case/Run-1');
  const filter = h.window.document.querySelector('.filter-input');
  filter.value = 'Rack_1';
  filter.dispatchEvent(new h.window.Event('input', { bubbles: true }));
  h.heads()[0].querySelector('input').click();
  const checks = Array.from(h.window.document.querySelectorAll('.pp-patch-cb'));
  assert.deepEqual(checks.map(cb => cb.checked), [false, false, true, true]);
  h.heads().forEach(head => assert.equal(head.nextElementSibling.style.display, 'none'));
});
