# Patch List Filter + Cross-Surface Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a substring/regex filter bar + 3D-linked multi-selection system to both patch-list panels in `STL_generate.html` (mesh-preview right panel and post-processing right panel).

**Architecture:** Two pure-JS modules (`patch_filter.js`, `patch_selection.js`) with zero DOM or Three.js dependency, plus thin per-site glue injected into the two existing build functions (`mpBuildPatchList` and `_ppBuildTree`). Purple emissive highlight with a `MeshBasicMaterial.color` fallback; bidirectional 3D viewport ↔ list row coupling via Three.js raycaster.

**Tech Stack:** Vanilla JS (ES2019), Three.js r128, Python (pytest regex-scan), Node 18+ `node:test` for pure-JS unit tests, browser-use CLI for smoke.

**Spec:** `docs/superpowers/specs/2026-04-18-patch-list-filter-selection-design.md`

---

## File Structure

**New files:**
- `STLGeneratorForOpenFoam/web/js/patch_filter.js` — `window.createFilterBar({container, onChange})`
- `STLGeneratorForOpenFoam/web/js/patch_selection.js` — `window.createSelectionModel({onChange})`
- `STLGeneratorForOpenFoam/scripts/test_patch_filter.mjs` — Node unit tests (predicate semantics, state round-trip)
- `STLGeneratorForOpenFoam/scripts/test_patch_selection.mjs` — Node unit tests (click handlers, diffs)
- `STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py` — structural regex-scan that wiring exists

**Modified files:**
- `STLGeneratorForOpenFoam/web/STL_generate.html`:
  - CSS block (near existing styles around L15): add `.patch-filter-bar`, `.row-selected`, `.filter-hidden`, `.filter-hidden-group`
  - `<script>` includes (around L856-860): add the two new JS files
  - `mpBuildPatchList()` L23675: insert filter bar + selection model + row-click glue
  - `mpLoadMesh()` L23471: `mpSelectionModel?.clear()` at the start
  - `_ppBuildTree()` L21060: symmetric integration
  - `ppInitPostProcess()` L21209: `ppSelectionModel?.clear()` at the start
  - Add `mpHighlightPatch`, `ppHighlightPatch`, `setPatchHighlight` (shared), `applyFilterToDOM`, `getVisibleOrderedNames`, `diffSets` helper block
  - Add `ensureMpViewportClickHandler`, `ensurePpViewportClickHandler` for 3D→list raycaster

All changes live in the submodule `STLGeneratorForOpenFoam/`; commits happen in that submodule. The parent repo only gets a submodule pointer bump at the end.

**Working directory convention:** all paths below are relative to `/mnt/lv/litianyu/mycode/dcdtuni`. Commands prefixed `(sub)` should run with CWD `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam`.

---

## Task 1: `createFilterBar` module with Node unit tests

**Files:**
- Create: `STLGeneratorForOpenFoam/web/js/patch_filter.js`
- Create: `STLGeneratorForOpenFoam/scripts/test_patch_filter.mjs`

- [ ] **Step 1: Write the failing test**

Create `STLGeneratorForOpenFoam/scripts/test_patch_filter.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="c"></div></body></html>');
global.window = dom.window;
global.document = dom.window.document;

// Load module (CommonJS-style side effect onto window)
await import('../web/js/patch_filter.js');
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd STLGeneratorForOpenFoam && node --test scripts/test_patch_filter.mjs`
Expected: fails with "Cannot find module" or "createFilterBar is not a function"

If `jsdom` is not installed:
```bash
cd STLGeneratorForOpenFoam && npm init -y 2>/dev/null; npm install --save-dev jsdom
```

- [ ] **Step 3: Implement `patch_filter.js`**

Create `STLGeneratorForOpenFoam/web/js/patch_filter.js`:

```js
(function () {
  'use strict';

  function buildPredicate(state) {
    const { text, case: caseOn, whole, regex } = state;
    if (!text) return () => true;

    if (regex) {
      try {
        const re = new RegExp(text, caseOn ? '' : 'i');
        return (name) => re.test(name);
      } catch (_) {
        return () => false;
      }
    }

    const haystackOf = caseOn ? (s) => s : (s) => s.toLowerCase();
    const needle = caseOn ? text : text.toLowerCase();

    if (whole) {
      return (name) => haystackOf(name) === needle;
    }
    return (name) => haystackOf(name).indexOf(needle) !== -1;
  }

  function isRegexValid(state) {
    if (!state.regex || !state.text) return true;
    try { new RegExp(state.text); return true; } catch (_) { return false; }
  }

  function createFilterBar({ container, onChange }) {
    const bar = document.createElement('div');
    bar.className = 'patch-filter-bar';
    bar.innerHTML = `
      <button class="btn-toggle" data-mode="case"  title="区分大小写 (Aa)">Aa</button>
      <button class="btn-toggle" data-mode="whole" title="全字匹配 (ab|)">ab|</button>
      <button class="btn-toggle" data-mode="regex" title="正则 (.*)">.*</button>
      <input type="text" class="filter-input" placeholder="过滤 patch..." />
      <span class="filter-count"></span>
    `;
    container.insertBefore(bar, container.firstChild);

    const input = bar.querySelector('.filter-input');
    const btns = {
      case:  bar.querySelector('[data-mode="case"]'),
      whole: bar.querySelector('[data-mode="whole"]'),
      regex: bar.querySelector('[data-mode="regex"]'),
    };

    const state = { text: '', case: false, whole: false, regex: false };

    function recompute() {
      input.classList.toggle('invalid', !isRegexValid(state));
      instance.predicate = buildPredicate(state);
      if (typeof onChange === 'function') onChange(instance.predicate);
    }

    input.addEventListener('input', () => {
      state.text = input.value;
      recompute();
    });

    Object.keys(btns).forEach((mode) => {
      btns[mode].addEventListener('click', () => {
        state[mode] = !state[mode];
        btns[mode].classList.toggle('active', state[mode]);
        recompute();
      });
    });

    const instance = {
      predicate: buildPredicate(state),
      getState: () => ({ ...state }),
      setState: (next) => {
        state.text = next.text || '';
        state.case = !!next.case;
        state.whole = !!next.whole;
        state.regex = !!next.regex;
        input.value = state.text;
        btns.case.classList.toggle('active', state.case);
        btns.whole.classList.toggle('active', state.whole);
        btns.regex.classList.toggle('active', state.regex);
        recompute();
      },
      clear: () => {
        state.text = '';
        state.case = false;
        state.whole = false;
        state.regex = false;
        input.value = '';
        btns.case.classList.remove('active');
        btns.whole.classList.remove('active');
        btns.regex.classList.remove('active');
        recompute();
      },
      updateCount: (matched, total) => {
        bar.querySelector('.filter-count').textContent = `${matched} / ${total}`;
      },
      element: bar,
    };

    return instance;
  }

  window.createFilterBar = createFilterBar;
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd STLGeneratorForOpenFoam && node --test scripts/test_patch_filter.mjs`
Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/patch_filter.js scripts/test_patch_filter.mjs package.json package-lock.json
git commit -m "feat(frontend): patch_filter.js — substring/regex filter bar module

Pure-JS module with zero dependencies outside JSDOM for unit tests.
Covers 9 behavioral cases: empty/substring/case/whole/regex/combined/
invalid-regex/state-round-trip/onChange-callback."
```

---

## Task 2: `createSelectionModel` module with Node unit tests

**Files:**
- Create: `STLGeneratorForOpenFoam/web/js/patch_selection.js`
- Create: `STLGeneratorForOpenFoam/scripts/test_patch_selection.mjs`

- [ ] **Step 1: Write the failing test**

Create `STLGeneratorForOpenFoam/scripts/test_patch_selection.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

global.window = new JSDOM('').window;
global.document = global.window.document;

await import('../web/js/patch_selection.js');
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd STLGeneratorForOpenFoam && node --test scripts/test_patch_selection.mjs`
Expected: fails with "createSelectionModel is not a function"

- [ ] **Step 3: Implement `patch_selection.js`**

Create `STLGeneratorForOpenFoam/web/js/patch_selection.js`:

```js
(function () {
  'use strict';

  function sliceRange(ordered, a, b) {
    const ia = ordered.indexOf(a);
    const ib = ordered.indexOf(b);
    if (ia < 0 || ib < 0) return null;
    const [lo, hi] = ia <= ib ? [ia, ib] : [ib, ia];
    return ordered.slice(lo, hi + 1);
  }

  function createSelectionModel({ onChange } = {}) {
    let selected = new Set();
    let anchor = null;

    function fire(prev) {
      if (typeof onChange === 'function') onChange(prev, new Set(selected));
    }

    function handleRowClick(name, { shift, ctrl, orderedNames }) {
      const prev = new Set(selected);

      if (shift && ctrl) {
        const range = anchor && sliceRange(orderedNames, anchor, name);
        if (!range) {
          selected = new Set([name]);
          anchor = name;
        } else {
          range.forEach((n) => selected.add(n));
          // anchor stays
        }
      } else if (shift) {
        const range = anchor && sliceRange(orderedNames, anchor, name);
        if (!range) {
          selected = new Set([name]);
          anchor = name;
        } else {
          selected = new Set(range);
          // anchor stays
        }
      } else if (ctrl) {
        if (selected.has(name)) {
          selected.delete(name);
          // anchor unchanged on remove
        } else {
          selected.add(name);
          anchor = name;
        }
      } else {
        selected = new Set([name]);
        anchor = name;
      }

      fire(prev);
    }

    function handleCheckboxClick(name, { shift, ctrl, nextVisible }) {
      const hasModifier = !!(shift || ctrl);
      if (hasModifier && selected.size > 0) {
        return { targets: [...selected], visible: nextVisible };
      }
      return { targets: [name], visible: nextVisible };
    }

    function setSelection(names) {
      const prev = new Set(selected);
      selected = new Set(names);
      anchor = names.length > 0 ? names[names.length - 1] : null;
      fire(prev);
    }

    function clear() {
      const prev = new Set(selected);
      if (prev.size === 0 && anchor === null) return;
      selected = new Set();
      anchor = null;
      fire(prev);
    }

    return {
      handleRowClick,
      handleCheckboxClick,
      setSelection,
      clear,
      has: (name) => selected.has(name),
      getAll: () => new Set(selected),
      getLastAnchor: () => anchor,
    };
  }

  window.createSelectionModel = createSelectionModel;
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd STLGeneratorForOpenFoam && node --test scripts/test_patch_selection.mjs`
Expected: all 12 tests pass.

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/patch_selection.js scripts/test_patch_selection.mjs
git commit -m "feat(frontend): patch_selection.js — multi-select model with Shift/Ctrl

Pure-JS selection state machine. Handles row-body click (plain/shift/
ctrl/shift+ctrl) and checkbox click (single vs batch). 12 behavioral
tests cover anchor semantics, range direction, modifier combinations,
and lifecycle (clear/setSelection)."
```

---

## Task 3: CSS + `<script>` tags + shared utility helpers

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — CSS near L15, `<script>` tags near L860, add utility block in JS section

- [ ] **Step 1: Add CSS rules**

Find the existing `<style>` block (first `<style>` tag near the top of the file, around line 15-800). Append these rules at the end of the `<style>` block, just before `</style>`:

```css
.patch-filter-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--panel-border, #333);
  background: var(--panel-bg-2, #1e1e1e);
  position: sticky;
  top: 0;
  z-index: 2;
}
.patch-filter-bar .btn-toggle {
  padding: 2px 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted, #888);
  cursor: pointer;
  font: 11px monospace;
  border-radius: 2px;
}
.patch-filter-bar .btn-toggle:hover { background: rgba(255,255,255,0.05); }
.patch-filter-bar .btn-toggle.active {
  border-color: var(--accent, #569cd6);
  color: var(--accent, #569cd6);
  background: rgba(86, 156, 214, 0.08);
}
.patch-filter-bar .filter-input {
  flex: 1;
  min-width: 0;
  padding: 2px 6px;
  font: 12px monospace;
  background: var(--input-bg, #111);
  color: var(--text, #ddd);
  border: 1px solid var(--panel-border, #333);
}
.patch-filter-bar .filter-input.invalid {
  border-color: #e06c75;
  background: rgba(224, 108, 117, 0.06);
}
.patch-filter-bar .filter-count {
  font: 10px monospace;
  color: var(--text-muted, #666);
  min-width: 40px;
  text-align: right;
}

.filter-hidden { display: none !important; }
.filter-hidden-group { display: none !important; }

[data-patch].row-selected {
  border-left: 3px solid #9b59d0;
  background: rgba(155, 89, 208, 0.08);
}
```

- [ ] **Step 2: Add `<script>` includes**

Find the script include block (around L854-860 after `theme/theme-loader.js`). Insert two new lines right after `js/liquid_mode.js`:

```html
<script src="theme/theme-loader.js"></script>
<script src="room_2d_editor.js"></script>
<script src="js/liquid_cooling.js"></script>
<script src="js/liquid_pipe_renderer.js"></script>
<script src="js/liquid_topology_editor.js"></script>
<script src="js/liquid_results_panel.js"></script>
<script src="js/liquid_mode.js"></script>
<script src="js/patch_filter.js"></script>
<script src="js/patch_selection.js"></script>
```

- [ ] **Step 3: Add shared utility helper block**

Locate the JS section of `STL_generate.html`. The block can go anywhere in the main `<script>` block — a good spot is right before the existing `mpBuildPatchList` function (search for `function mpBuildPatchList` to find L23675). Insert this block:

```js
// =====================================================================
// Patch-list filter & selection shared helpers (2026-04-18)
// =====================================================================

const PATCH_HIGHLIGHT_COLOR = 0x9b59d0;

function applyFilterToDOM(container, predicate, filterBar) {
  const rows = container.querySelectorAll('[data-patch]');
  const groupParents = new Set();
  let matched = 0;
  rows.forEach((row) => {
    const ok = predicate(row.dataset.patch);
    row.classList.toggle('filter-hidden', !ok);
    if (ok) matched += 1;
    if (row.parentElement && row.parentElement !== container) {
      groupParents.add(row.parentElement);
    }
  });
  groupParents.forEach((group) => {
    const anyVisible = Array.from(group.querySelectorAll('[data-patch]'))
      .some((r) => !r.classList.contains('filter-hidden'));
    group.classList.toggle('filter-hidden-group', !anyVisible);
  });
  if (filterBar) filterBar.updateCount(matched, rows.length);
}

function getVisibleOrderedNames(container) {
  return Array.from(container.querySelectorAll('[data-patch]'))
    .filter((r) => !r.classList.contains('filter-hidden'))
    .map((r) => r.dataset.patch);
}

function diffSets(prev, curr, { added, removed }) {
  prev.forEach((n) => { if (!curr.has(n) && removed) removed(n); });
  curr.forEach((n) => { if (!prev.has(n) && added) added(n); });
}

function setPatchHighlight(mesh, on) {
  if (!mesh || !mesh.material) return;
  const m = mesh.material;
  if (on) {
    if (mesh.userData._origEmissive === undefined) {
      if (m.emissive) {
        mesh.userData._origEmissive = m.emissive.getHex();
        mesh.userData._origEmissiveIntensity = m.emissiveIntensity ?? 1;
        mesh.userData._fallbackMode = false;
      } else if (m.color) {
        mesh.userData._origEmissive = null; // marker for fallback path
        mesh.userData._origColor = m.color.getHex();
        mesh.userData._fallbackMode = true;
      } else {
        return;
      }
    }
    if (mesh.userData._fallbackMode) {
      m.color.setHex(PATCH_HIGHLIGHT_COLOR);
    } else {
      m.emissive.setHex(PATCH_HIGHLIGHT_COLOR);
      m.emissiveIntensity = 0.6;
    }
  } else {
    if (mesh.userData._origEmissive === undefined) return; // was never highlighted
    if (mesh.userData._fallbackMode) {
      m.color.setHex(mesh.userData._origColor);
    } else {
      m.emissive.setHex(mesh.userData._origEmissive);
      m.emissiveIntensity = mesh.userData._origEmissiveIntensity;
    }
    mesh.userData._origEmissive = undefined;
  }
  m.needsUpdate = true;
}

function mpHighlightPatch(name, on) {
  const entry = state.patchMeshes && state.patchMeshes[name];
  if (!entry) return;
  // Mesh preview entries are {surface, edges}; guard in case one is missing.
  if (entry.surface) setPatchHighlight(entry.surface, on);
  if (entry.edges && entry.edges.material) {
    const edgeM = entry.edges.material;
    if (on) {
      if (entry.edges.userData._origEdgeColor === undefined) {
        entry.edges.userData._origEdgeColor = edgeM.color.getHex();
      }
      edgeM.color.setHex(PATCH_HIGHLIGHT_COLOR);
    } else if (entry.edges.userData._origEdgeColor !== undefined) {
      edgeM.color.setHex(entry.edges.userData._origEdgeColor);
      entry.edges.userData._origEdgeColor = undefined;
    }
    edgeM.needsUpdate = true;
  }
}

function ppHighlightPatch(name, on) {
  setPatchHighlight(state.patchMeshes && state.patchMeshes[name], on);
}

// Module-scoped per-site singletons (populated by build fns below)
let mpFilterBar = null;
let mpSelectionModel = null;
let ppFilterBar = null;
let ppSelectionModel = null;

// Track whether viewport click handlers have been attached (once per site)
let mpViewportClickWired = false;
let ppViewportClickWired = false;
```

- [ ] **Step 4: Verify the file still parses**

Run:
```bash
cd STLGeneratorForOpenFoam
python3 -c "
from pathlib import Path
html = Path('web/STL_generate.html').read_text()
# Sanity: counts should still be balanced-ish
assert html.count('<script') == html.count('</script>'), 'script tag mismatch'
assert 'function mpBuildPatchList' in html
assert 'function _ppBuildTree' in html
assert 'PATCH_HIGHLIGHT_COLOR' in html
print('OK')
"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): scaffold patch-list filter/select CSS + helpers

Added CSS classes (patch-filter-bar, row-selected, filter-hidden,
filter-hidden-group), script includes for the two new modules, and a
shared helper block (applyFilterToDOM, getVisibleOrderedNames, diffSets,
setPatchHighlight, mpHighlightPatch, ppHighlightPatch, site singletons).
No build-fn integration yet; wired in Tasks 4-5."
```

---

## Task 4: Mesh-preview site integration (filter + selection + row-click + highlight)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — inside `mpBuildPatchList()` L23675, inside `mpLoadMesh()` L23471

- [ ] **Step 1: Add `mpSelectionModel.clear()` to `mpLoadMesh`**

Search for `function mpLoadMesh(` to locate L23471. Find the opening brace of the function body. Insert at the very top of the body (before any existing logic):

```js
function mpLoadMesh(...args) {
  // Lifecycle reset: clear selection before the new list is built.
  if (mpSelectionModel) mpSelectionModel.clear();
  // ...existing body unchanged...
```

(The engineer: match the existing function signature, do not change args; only insert the clear line.)

- [ ] **Step 2: Integrate glue into `mpBuildPatchList`**

Search for `function mpBuildPatchList(` at L23675. At the **end** of the function body (right before the closing `}`), add the glue. If the function returns a value, put the glue before the return statement.

```js
// ================ patch-list filter + selection glue (mesh-preview) ================
const mpContainer = document.getElementById('mp-patch-list');
if (mpContainer) {
  // Preserve state across rebuilds
  const prevFilter = mpFilterBar ? mpFilterBar.getState() : null;
  const prevSelection = mpSelectionModel ? new Set(mpSelectionModel.getAll()) : new Set();

  // Strip any leftover filter bar DOM from a prior render (tree-rebuild path)
  const oldBar = mpContainer.querySelector('.patch-filter-bar');
  if (oldBar) oldBar.remove();

  // Recreate filter bar (closure captures the new bar reference; apply predicate)
  mpFilterBar = createFilterBar({
    container: mpContainer,
    onChange: (predicate) => applyFilterToDOM(mpContainer, predicate, mpFilterBar),
  });
  if (prevFilter) mpFilterBar.setState(prevFilter);

  // Create selection model once
  if (!mpSelectionModel) {
    mpSelectionModel = createSelectionModel({
      onChange: (prev, curr) => {
        diffSets(prev, curr, {
          added: (name) => {
            const row = mpContainer.querySelector(`[data-patch="${CSS.escape(name)}"]`);
            if (row) row.classList.add('row-selected');
            mpHighlightPatch(name, true);
          },
          removed: (name) => {
            const row = mpContainer.querySelector(`[data-patch="${CSS.escape(name)}"]`);
            if (row) row.classList.remove('row-selected');
            mpHighlightPatch(name, false);
          },
        });
      },
    });
  }

  // Wire row-level click listeners
  mpContainer.querySelectorAll('[data-patch]').forEach((row) => {
    if (row.dataset._mpClickWired) return;
    row.dataset._mpClickWired = '1';
    row.addEventListener('click', (e) => {
      const name = row.dataset.patch;
      const shift = e.shiftKey;
      const ctrl = e.ctrlKey || e.metaKey;
      const isCheckbox = e.target.matches('input[type=checkbox]');

      if (isCheckbox) {
        const cb = e.target;
        // Browser has already toggled cb.checked by the time click fires
        const nextVisible = cb.checked;
        const { targets, visible } = mpSelectionModel.handleCheckboxClick(name, {
          shift, ctrl, nextVisible,
        });
        targets.forEach((n) => {
          const otherRow = mpContainer.querySelector(`[data-patch="${CSS.escape(n)}"]`);
          const otherCb = otherRow && otherRow.querySelector('input[type=checkbox]');
          if (otherCb) otherCb.checked = visible;
          if (typeof mpUpdateVisibility === 'function') mpUpdateVisibility(n, visible);
        });
      } else {
        const orderedNames = getVisibleOrderedNames(mpContainer);
        mpSelectionModel.handleRowClick(name, { shift, ctrl, orderedNames });
      }
    });
  });

  // Restore prior selection where names still exist
  const currentNames = new Set(
    Array.from(mpContainer.querySelectorAll('[data-patch]')).map((r) => r.dataset.patch)
  );
  const restored = [...prevSelection].filter((n) => currentNames.has(n));
  if (restored.length) mpSelectionModel.setSelection(restored);

  // Re-apply filter (updates counts + hidden classes)
  applyFilterToDOM(mpContainer, mpFilterBar.predicate, mpFilterBar);
}
// ================ end glue ================
```

- [ ] **Step 3: Verify `mpUpdateVisibility` signature**

The glue assumes `mpUpdateVisibility(name, visible)`. Search for `function mpUpdateVisibility(` to check.

Run:
```bash
cd STLGeneratorForOpenFoam
grep -n "function mpUpdateVisibility\|mpUpdateVisibility(" web/STL_generate.html | head -10
```

Expected: the definition line shows `function mpUpdateVisibility(name, visible)` or similar two-arg `(patchName, isVisible)`. If the signature differs, adapt the glue's call site.

- [ ] **Step 4: Smoke-test in browser-use**

Requires server running. Start if needed:
```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py \
  --case-dir case/base_leakage > /tmp/stlgen-srv.log 2>&1 &
sleep 3
```

Then:
```bash
export PATH="$HOME/.local/bin:$PATH"
# Log in (admin/WIN#@desk) via the cookie helper, then:
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "document.querySelector('[data-tab=\"mesh\"]')?.click()"
# Click "显示网格" button (find its data-* / id from mpLoadMesh trigger)
browser-use eval "document.querySelector('#mp-load-mesh-btn')?.click() || 'no btn'"
# Wait for list to render
sleep 4
browser-use eval "(() => {
  const bar = document.querySelector('#mp-patch-list .patch-filter-bar');
  const input = bar?.querySelector('.filter-input');
  if (!bar || !input) return 'missing bar';
  input.value = 'wall';
  input.dispatchEvent(new Event('input'));
  return {
    hidden: document.querySelectorAll('#mp-patch-list [data-patch].filter-hidden').length,
    visible: document.querySelectorAll('#mp-patch-list [data-patch]:not(.filter-hidden)').length,
  };
})()"
browser-use screenshot /tmp/mp-filter.png
browser-use close
```

Expected: the return value shows some rows hidden + some visible; screenshot shows the bar rendered and narrowed list.

Read `/tmp/mp-filter.png` with the Read tool to confirm the filter bar appears at the top and row-selected class is absent (no selection made yet).

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): wire filter+selection into mesh-preview patch list

Integrates filter bar, selection model, row-click handlers, and purple
highlight into mpBuildPatchList. mpLoadMesh calls selectionModel.clear()
at lifecycle boundary. Verified via browser-use smoke: filter hides
non-matching rows, count updates, click handlers don't double-wire."
```

---

## Task 5: Post-processing site integration (symmetric)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — inside `_ppBuildTree()` L21060, inside `ppInitPostProcess()` L21209

- [ ] **Step 1: Add `ppSelectionModel.clear()` to `ppInitPostProcess`**

Search for `function ppInitPostProcess(` to locate L21209. Insert at top of body:

```js
function ppInitPostProcess(...args) {
  if (ppSelectionModel) ppSelectionModel.clear();
  // ...existing body unchanged...
```

- [ ] **Step 2: Integrate glue into `_ppBuildTree`**

Search for `function _ppBuildTree(` at L21060. Append at the end of the function body:

```js
// ================ patch-list filter + selection glue (post-processing) ================
const ppContainer = document.getElementById('pp-tree');
if (ppContainer) {
  const prevFilter = ppFilterBar ? ppFilterBar.getState() : null;
  const prevSelection = ppSelectionModel ? new Set(ppSelectionModel.getAll()) : new Set();

  const oldBar = ppContainer.querySelector('.patch-filter-bar');
  if (oldBar) oldBar.remove();

  ppFilterBar = createFilterBar({
    container: ppContainer,
    onChange: (predicate) => applyFilterToDOM(ppContainer, predicate, ppFilterBar),
  });
  if (prevFilter) ppFilterBar.setState(prevFilter);

  if (!ppSelectionModel) {
    ppSelectionModel = createSelectionModel({
      onChange: (prev, curr) => {
        diffSets(prev, curr, {
          added: (name) => {
            const row = ppContainer.querySelector(`[data-patch="${CSS.escape(name)}"]`);
            if (row) row.classList.add('row-selected');
            ppHighlightPatch(name, true);
          },
          removed: (name) => {
            const row = ppContainer.querySelector(`[data-patch="${CSS.escape(name)}"]`);
            if (row) row.classList.remove('row-selected');
            ppHighlightPatch(name, false);
          },
        });
      },
    });
  }

  ppContainer.querySelectorAll('[data-patch]').forEach((row) => {
    if (row.dataset._ppClickWired) return;
    row.dataset._ppClickWired = '1';
    row.addEventListener('click', (e) => {
      const name = row.dataset.patch;
      const shift = e.shiftKey;
      const ctrl = e.ctrlKey || e.metaKey;
      const isCheckbox = e.target.matches('input[type=checkbox]');

      if (isCheckbox) {
        const cb = e.target;
        const nextVisible = cb.checked;
        const { targets, visible } = ppSelectionModel.handleCheckboxClick(name, {
          shift, ctrl, nextVisible,
        });
        targets.forEach((n) => {
          const otherRow = ppContainer.querySelector(`[data-patch="${CSS.escape(n)}"]`);
          const otherCb = otherRow && otherRow.querySelector('input[type=checkbox]');
          if (otherCb) otherCb.checked = visible;
          if (typeof ppUpdatePatchVisibility === 'function') ppUpdatePatchVisibility(n, visible);
        });
      } else {
        const orderedNames = getVisibleOrderedNames(ppContainer);
        ppSelectionModel.handleRowClick(name, { shift, ctrl, orderedNames });
      }
    });
  });

  const currentNames = new Set(
    Array.from(ppContainer.querySelectorAll('[data-patch]')).map((r) => r.dataset.patch)
  );
  const restored = [...prevSelection].filter((n) => currentNames.has(n));
  if (restored.length) ppSelectionModel.setSelection(restored);

  applyFilterToDOM(ppContainer, ppFilterBar.predicate, ppFilterBar);
}
// ================ end glue ================
```

- [ ] **Step 3: Verify `ppUpdatePatchVisibility` signature**

Run:
```bash
cd STLGeneratorForOpenFoam
grep -n "function ppUpdatePatchVisibility\|ppUpdatePatchVisibility(" web/STL_generate.html | head -5
```

Expected: two-arg signature `(name, visible)` or similar. Adapt if different.

- [ ] **Step 4: Smoke-test**

Assumes server is running and a finished solve record exists in `case/base_leakage/`.

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# Switch to post-processing tab + init (exact trigger depends on existing UI)
browser-use eval "document.querySelector('[data-tab=\"postprocess\"]')?.click()"
# Trigger ppInitPostProcess (whatever button invokes it)
browser-use eval "typeof ppInitPostProcess === 'function' ? ppInitPostProcess() : 'missing'"
sleep 4
browser-use eval "(() => {
  const bar = document.querySelector('#pp-tree .patch-filter-bar');
  if (!bar) return 'missing bar';
  bar.querySelector('.filter-input').value = 'fan';
  bar.querySelector('.filter-input').dispatchEvent(new Event('input'));
  return {
    hidden: document.querySelectorAll('#pp-tree [data-patch].filter-hidden').length,
    visible: document.querySelectorAll('#pp-tree [data-patch]:not(.filter-hidden)').length,
  };
})()"
browser-use screenshot /tmp/pp-filter.png
browser-use close
```

Expected: filter works identically to mesh-preview. Screenshot confirms.

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): wire filter+selection into post-processing patch list

Symmetric integration of filter bar, selection model, row-click, and
purple highlight into _ppBuildTree. ppInitPostProcess clears selection
at lifecycle boundary. Verified via browser-use."
```

---

## Task 6: Mesh-preview viewport click → list selection

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — inside `mpBuildPatchList()`, add viewport click wiring; add new `ensureMpViewportClickHandler` fn

- [ ] **Step 1: Find the mesh-preview renderer + camera references**

The mesh-preview panel uses Three.js. Search for the renderer instance used by the preview:
```bash
cd STLGeneratorForOpenFoam
grep -n "mpRenderer\|mp_renderer\|mesh-preview.*renderer\|mpCamera" web/STL_generate.html | head -20
```

Identify the global names. The glue below assumes `mpRenderer`, `mpCamera`, and `mpScene`. **If the existing code uses different names (e.g. `state.mpRenderer` or `meshPreviewRenderer`), adapt the names throughout Step 3.**

- [ ] **Step 2: Add `ensureMpViewportClickHandler` function**

Add to the shared helper block (next to `mpHighlightPatch` from Task 3). A convenient spot is right before `// Module-scoped per-site singletons`:

```js
const _mpRaycaster = new THREE.Raycaster();
const _mpNdc = new THREE.Vector2();

function _mpCollectSelectableMeshes() {
  const out = [];
  if (!state.patchMeshes) return out;
  Object.values(state.patchMeshes).forEach((entry) => {
    if (entry && entry.surface) out.push(entry.surface);
  });
  return out;
}

function _mpResolveMeshToName(object) {
  if (!state.patchMeshes) return null;
  for (const [name, entry] of Object.entries(state.patchMeshes)) {
    if (entry && entry.surface === object) return name;
  }
  return null;
}

function ensureMpViewportClickHandler() {
  if (mpViewportClickWired) return;
  if (typeof mpRenderer === 'undefined' || !mpRenderer || !mpCamera) return;
  mpViewportClickWired = true;

  let downX = 0, downY = 0;
  const DRAG_THRESHOLD_PX = 3;

  mpRenderer.domElement.addEventListener('mousedown', (e) => {
    downX = e.clientX; downY = e.clientY;
  });

  mpRenderer.domElement.addEventListener('mouseup', (e) => {
    if (Math.abs(e.clientX - downX) > DRAG_THRESHOLD_PX ||
        Math.abs(e.clientY - downY) > DRAG_THRESHOLD_PX) return;
    if (!mpSelectionModel) return;

    const container = document.getElementById('mp-patch-list');
    if (!container) return;

    const rect = mpRenderer.domElement.getBoundingClientRect();
    _mpNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    _mpNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    _mpRaycaster.setFromCamera(_mpNdc, mpCamera);

    const hit = _mpRaycaster.intersectObjects(_mpCollectSelectableMeshes(), false)[0];
    if (!hit) {
      if (!e.shiftKey && !(e.ctrlKey || e.metaKey)) mpSelectionModel.clear();
      return;
    }
    const name = _mpResolveMeshToName(hit.object);
    if (!name) return;

    const orderedNames = getVisibleOrderedNames(container);
    mpSelectionModel.handleRowClick(name, {
      shift: e.shiftKey,
      ctrl: e.ctrlKey || e.metaKey,
      orderedNames,
    });

    const row = container.querySelector(`[data-patch="${CSS.escape(name)}"]`);
    if (row && !row.classList.contains('filter-hidden')) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  });
}
```

- [ ] **Step 3: Call `ensureMpViewportClickHandler()` from `mpBuildPatchList`**

Inside the glue block added in Task 4, just before the last `applyFilterToDOM` line, add:

```js
  // Attach viewport 3D→list handler once (no-op on subsequent rebuilds)
  ensureMpViewportClickHandler();

  applyFilterToDOM(mpContainer, mpFilterBar.predicate, mpFilterBar);
}
```

- [ ] **Step 4: Smoke-test**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "document.querySelector('[data-tab=\"mesh\"]')?.click()"
browser-use eval "document.querySelector('#mp-load-mesh-btn')?.click() || 'no btn'"
sleep 4
# Programmatically simulate a click inside the 3D canvas
browser-use eval "(() => {
  const canvas = mpRenderer.domElement;
  const r = canvas.getBoundingClientRect();
  const ev = (type) => new MouseEvent(type, {
    clientX: r.left + r.width/2, clientY: r.top + r.height/2, bubbles: true,
  });
  canvas.dispatchEvent(ev('mousedown'));
  canvas.dispatchEvent(ev('mouseup'));
  return {
    selected: [...mpSelectionModel.getAll()],
    rowSelectedCount: document.querySelectorAll('#mp-patch-list .row-selected').length,
  };
})()"
browser-use screenshot /tmp/mp-3d-select.png
browser-use close
```

Expected: `selected` has one name + `rowSelectedCount >= 1`. Screenshot shows the corresponding row highlighted and the 3D geometry tinted purple.

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): 3D viewport click → mesh-preview list selection

Three.js raycaster on mpRenderer.domElement with 3px drag threshold to
coexist with OrbitControls. On hit, routes through mpSelectionModel
(respecting shift/ctrl), and scrollIntoView for the matched row.
Missing hit with no modifier clears selection (3D-app convention)."
```

---

## Task 7: Post-processing viewport click → list selection

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — add `ensurePpViewportClickHandler`, call from `_ppBuildTree`

- [ ] **Step 1: Find pp renderer/camera**

```bash
cd STLGeneratorForOpenFoam
grep -n "ppRenderer\|ppScene\|ppCamera\|pp_renderer" web/STL_generate.html | head -20
```

Identify the global names (likely `ppRenderer`, `ppCamera` or similar). Adapt below.

- [ ] **Step 2: Add `ensurePpViewportClickHandler`**

Add to the shared helper block (near `ensureMpViewportClickHandler`):

```js
const _ppRaycaster = new THREE.Raycaster();
const _ppNdc = new THREE.Vector2();

function _ppCollectSelectableMeshes() {
  const out = [];
  if (!state.patchMeshes) return out;
  Object.values(state.patchMeshes).forEach((mesh) => {
    // In pp, patchMeshes[name] is a single mesh (not {surface,edges}).
    if (mesh && mesh.isMesh) out.push(mesh);
  });
  return out;
}

function _ppResolveMeshToName(object) {
  // pp meshes already have _ppPatchName set (L21377 in existing code).
  return object && object._ppPatchName ? object._ppPatchName : null;
}

function ensurePpViewportClickHandler() {
  if (ppViewportClickWired) return;
  if (typeof ppRenderer === 'undefined' || !ppRenderer || !ppCamera) return;
  ppViewportClickWired = true;

  let downX = 0, downY = 0;
  const DRAG_THRESHOLD_PX = 3;

  ppRenderer.domElement.addEventListener('mousedown', (e) => {
    downX = e.clientX; downY = e.clientY;
  });

  ppRenderer.domElement.addEventListener('mouseup', (e) => {
    if (Math.abs(e.clientX - downX) > DRAG_THRESHOLD_PX ||
        Math.abs(e.clientY - downY) > DRAG_THRESHOLD_PX) return;
    if (!ppSelectionModel) return;

    // Defer if an active picking tool is in use (slice, streamline, etc.)
    if (typeof ppActivePickingTool === 'string' && ppActivePickingTool) return;

    const container = document.getElementById('pp-tree');
    if (!container) return;

    const rect = ppRenderer.domElement.getBoundingClientRect();
    _ppNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    _ppNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    _ppRaycaster.setFromCamera(_ppNdc, ppCamera);

    const hit = _ppRaycaster.intersectObjects(_ppCollectSelectableMeshes(), false)[0];
    if (!hit) {
      if (!e.shiftKey && !(e.ctrlKey || e.metaKey)) ppSelectionModel.clear();
      return;
    }
    const name = _ppResolveMeshToName(hit.object);
    if (!name) return;

    const orderedNames = getVisibleOrderedNames(container);
    ppSelectionModel.handleRowClick(name, {
      shift: e.shiftKey,
      ctrl: e.ctrlKey || e.metaKey,
      orderedNames,
    });

    const row = container.querySelector(`[data-patch="${CSS.escape(name)}"]`);
    if (row && !row.classList.contains('filter-hidden')) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  });
}
```

Note the `ppActivePickingTool` guard is a defensive check — if no such variable exists in the codebase, `typeof` returns `'undefined'` and the guard is a no-op. No failure.

- [ ] **Step 3: Call from `_ppBuildTree`**

Inside the glue added in Task 5, just before the final `applyFilterToDOM` call, insert:

```js
  ensurePpViewportClickHandler();

  applyFilterToDOM(ppContainer, ppFilterBar.predicate, ppFilterBar);
}
```

- [ ] **Step 4: Smoke-test**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "document.querySelector('[data-tab=\"postprocess\"]')?.click()"
browser-use eval "typeof ppInitPostProcess === 'function' ? ppInitPostProcess() : 'missing'"
sleep 4
browser-use eval "(() => {
  const canvas = ppRenderer.domElement;
  const r = canvas.getBoundingClientRect();
  const ev = (type) => new MouseEvent(type, {
    clientX: r.left + r.width/2, clientY: r.top + r.height/2, bubbles: true,
  });
  canvas.dispatchEvent(ev('mousedown'));
  canvas.dispatchEvent(ev('mouseup'));
  return {
    selected: [...ppSelectionModel.getAll()],
    rowSelectedCount: document.querySelectorAll('#pp-tree .row-selected').length,
  };
})()"
browser-use screenshot /tmp/pp-3d-select.png
browser-use close
```

Expected: `selected` has one name (hit via raycast on centre), row highlighted.

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): 3D viewport click → post-processing list selection

Symmetric raycaster for pp viewport with 3px drag threshold. Uses the
existing mesh._ppPatchName marker (L21377) for reverse lookup. Guards
against active picking tools via optional ppActivePickingTool var."
```

---

## Task 8: Python regex-scan structural test

**Files:**
- Create: `STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py`

- [ ] **Step 1: Write the test**

Create `STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py`:

```python
"""Structural assertions that the patch-list filter + selection wiring
exists inside STL_generate.html. Mirrors the regex-scan pattern used by
test_refinement_redesign_frontend.py — does NOT require a running server.

Spec: docs/superpowers/specs/2026-04-18-patch-list-filter-selection-design.md
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "STL_generate.html"
HTML = HTML_PATH.read_text(encoding="utf-8")

WEB_JS = HTML_PATH.parent / "js"
FILTER_JS = (WEB_JS / "patch_filter.js").read_text(encoding="utf-8")
SELECTION_JS = (WEB_JS / "patch_selection.js").read_text(encoding="utf-8")


def extract_fn_body(name: str) -> str | None:
    m = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", HTML)
    if not m:
        m = re.search(rf"window\.{re.escape(name)}\s*=\s*function\s*\([^)]*\)\s*\{{", HTML)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    for i, ch in enumerate(HTML[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[start + 1 : i]
    return None


def test_patch_filter_module_exposes_createFilterBar():
    assert "window.createFilterBar" in FILTER_JS


def test_patch_selection_module_exposes_createSelectionModel():
    assert "window.createSelectionModel" in SELECTION_JS


def test_script_tags_include_new_modules():
    assert 'src="js/patch_filter.js"' in HTML
    assert 'src="js/patch_selection.js"' in HTML


def test_css_rules_defined():
    for cls in (".patch-filter-bar", ".row-selected", ".filter-hidden",
                ".filter-hidden-group", ".filter-input.invalid"):
        assert cls in HTML, f"CSS rule {cls} missing"


def test_shared_helpers_defined_in_html():
    for name in ("applyFilterToDOM", "getVisibleOrderedNames", "diffSets",
                 "setPatchHighlight", "mpHighlightPatch", "ppHighlightPatch",
                 "PATCH_HIGHLIGHT_COLOR"):
        assert name in HTML, f"helper {name} missing from STL_generate.html"


def test_module_scoped_singletons_declared():
    assert re.search(r"let\s+mpFilterBar\s*=\s*null", HTML)
    assert re.search(r"let\s+mpSelectionModel\s*=\s*null", HTML)
    assert re.search(r"let\s+ppFilterBar\s*=\s*null", HTML)
    assert re.search(r"let\s+ppSelectionModel\s*=\s*null", HTML)


def test_mesh_preview_build_fn_integrates():
    body = extract_fn_body("mpBuildPatchList")
    assert body, "mpBuildPatchList not found"
    for token in ("createFilterBar", "createSelectionModel",
                  "mpFilterBar", "mpSelectionModel",
                  "handleRowClick", "handleCheckboxClick",
                  "ensureMpViewportClickHandler",
                  "applyFilterToDOM"):
        assert token in body, f"mpBuildPatchList missing {token}"


def test_post_processing_build_fn_integrates():
    body = extract_fn_body("_ppBuildTree")
    assert body, "_ppBuildTree not found"
    for token in ("createFilterBar", "createSelectionModel",
                  "ppFilterBar", "ppSelectionModel",
                  "handleRowClick", "handleCheckboxClick",
                  "ensurePpViewportClickHandler",
                  "applyFilterToDOM"):
        assert token in body, f"_ppBuildTree missing {token}"


def test_lifecycle_resets_call_clear():
    mp_load = extract_fn_body("mpLoadMesh")
    pp_init = extract_fn_body("ppInitPostProcess")
    assert mp_load, "mpLoadMesh not found"
    assert pp_init, "ppInitPostProcess not found"
    assert "mpSelectionModel" in mp_load and ".clear()" in mp_load, \
        "mpLoadMesh must call mpSelectionModel.clear() on entry"
    assert "ppSelectionModel" in pp_init and ".clear()" in pp_init, \
        "ppInitPostProcess must call ppSelectionModel.clear() on entry"


def test_highlight_helpers_use_shared_color_constant():
    assert "PATCH_HIGHLIGHT_COLOR" in HTML
    # setPatchHighlight sources the color from the constant
    setfn = extract_fn_body("setPatchHighlight")
    assert setfn, "setPatchHighlight not found"
    assert "PATCH_HIGHLIGHT_COLOR" in setfn


def test_meshBasic_fallback_branch_present():
    setfn = extract_fn_body("setPatchHighlight")
    assert setfn
    # Covers the "m.color" fallback path for materials without emissive
    assert "_fallbackMode" in setfn
    assert "_origColor" in setfn


def test_drag_threshold_guard_applied_in_both_viewport_handlers():
    mp = extract_fn_body("ensureMpViewportClickHandler")
    pp = extract_fn_body("ensurePpViewportClickHandler")
    assert mp and pp
    for body in (mp, pp):
        assert "DRAG_THRESHOLD_PX" in body
        assert "mousedown" in body
        assert "mouseup" in body


def test_viewport_empty_space_click_clears_selection():
    mp = extract_fn_body("ensureMpViewportClickHandler")
    pp = extract_fn_body("ensurePpViewportClickHandler")
    assert mp and pp
    # Both handlers must call .clear() in the no-hit + no-modifier branch
    for body in (mp, pp):
        assert ".clear()" in body


def test_scroll_into_view_skipped_for_filter_hidden_rows():
    mp = extract_fn_body("ensureMpViewportClickHandler")
    pp = extract_fn_body("ensurePpViewportClickHandler")
    for body in (mp, pp):
        assert "filter-hidden" in body
        assert "scrollIntoView" in body
```

- [ ] **Step 2: Run the test**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py -v
```

Expected: all 13 tests pass.

If a test fails, the diagnostic tells you which integration step was missed; go back to the corresponding Task (4/5/6/7) and fix.

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add scripts/test_patch_list_frontend.py
git commit -m "test(frontend): structural assertions for patch-list filter+selection

13 regex-scan tests covering module exposure, script-tag inclusion, CSS
rules, shared helpers, per-site glue integration into mpBuildPatchList
and _ppBuildTree, lifecycle reset wiring, highlight color sharing,
MeshBasic fallback branch, and viewport-click behavior."
```

---

## Task 9: End-to-end browser-use smoke + final commit

**Files:** none modified — verification only

- [ ] **Step 1: Full regression — Node + Python tests**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
node --test scripts/test_patch_filter.mjs scripts/test_patch_selection.mjs
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest \
  STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py \
  STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py \
  src/backend/aircooling/runner/tests/ -v 2>&1 | tail -20
```

Expected: 9 + 12 + 13 + 18 (refinement-redesign) + 106 (runner) = 158 passed, 4 pre-existing failures in `test_partition_hole_fvoptions.py` are unrelated.

- [ ] **Step 2: Browser-use smoke — full 9-step sequence from spec §9.3**

Start server if not running:
```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
cd /mnt/lv/litianyu/mycode/dcdtuni
nohup conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py \
  --case-dir case/base_leakage > /tmp/stlgen-srv.log 2>&1 &
disown
sleep 3
# Log in via cookie
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}'
```

Run the 9-step sequence in browser-use. For each step, take a screenshot and visually verify via Read tool:

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# (1) Mesh tab → show mesh → filter "wall"
browser-use eval "document.querySelector('[data-tab=\"mesh\"]')?.click()"
browser-use eval "document.querySelector('#mp-load-mesh-btn')?.click()"
sleep 5
browser-use eval "(() => {
  const inp = document.querySelector('#mp-patch-list .filter-input');
  inp.value = 'wall'; inp.dispatchEvent(new Event('input'));
  return document.querySelector('.filter-count').textContent;
})()"
browser-use screenshot /tmp/smoke-1-filter-wall.png
# (2) Toggle regex, "^fan"
browser-use eval "(() => {
  document.querySelector('[data-mode=\"regex\"]').click();
  const inp = document.querySelector('#mp-patch-list .filter-input');
  inp.value = '^fan'; inp.dispatchEvent(new Event('input'));
  return document.querySelector('.filter-count').textContent;
})()"
browser-use screenshot /tmp/smoke-2-regex-fan.png
# (3) Clear filter, click first row → purple highlight
browser-use eval "(() => {
  document.querySelector('[data-mode=\"regex\"]').click();
  document.querySelector('#mp-patch-list .filter-input').value = '';
  document.querySelector('#mp-patch-list .filter-input').dispatchEvent(new Event('input'));
  const r = document.querySelector('#mp-patch-list [data-patch]');
  r.click();
  return { selected: [...mpSelectionModel.getAll()] };
})()"
browser-use screenshot /tmp/smoke-3-single-select.png
# (4) Shift-click a later row
browser-use eval "(() => {
  const rows = document.querySelectorAll('#mp-patch-list [data-patch]');
  rows[4].dispatchEvent(new MouseEvent('click', {shiftKey: true, bubbles: true}));
  return [...mpSelectionModel.getAll()];
})()"
browser-use screenshot /tmp/smoke-4-shift-range.png
# (5) Shift-click a selected checkbox → batch visibility
browser-use eval "(() => {
  const rows = document.querySelectorAll('#mp-patch-list [data-patch].row-selected');
  const cb = rows[0].querySelector('input[type=checkbox]');
  // Uncheck first to establish state, then shift-click to batch
  cb.checked = false;
  cb.dispatchEvent(new MouseEvent('click', {shiftKey: true, bubbles: true}));
  return [...document.querySelectorAll('#mp-patch-list [data-patch].row-selected')]
    .map(r => r.querySelector('input').checked);
})()"
browser-use screenshot /tmp/smoke-5-batch-visibility.png
# (6) Click 3D viewport → row scrolls + highlights
browser-use eval "(() => {
  mpSelectionModel.clear();
  const canvas = mpRenderer.domElement;
  const r = canvas.getBoundingClientRect();
  const ev = (t) => new MouseEvent(t, {clientX: r.left+r.width/2, clientY: r.top+r.height/2, bubbles:true});
  canvas.dispatchEvent(ev('mousedown'));
  canvas.dispatchEvent(ev('mouseup'));
  return [...mpSelectionModel.getAll()];
})()"
browser-use screenshot /tmp/smoke-6-3d-click.png
# (7) Click empty space → clear
browser-use eval "(() => {
  const canvas = mpRenderer.domElement;
  const r = canvas.getBoundingClientRect();
  // Click far corner (likely miss all geometry)
  const ev = (t) => new MouseEvent(t, {clientX: r.left+5, clientY: r.top+5, bubbles:true});
  canvas.dispatchEvent(ev('mousedown'));
  canvas.dispatchEvent(ev('mouseup'));
  return [...mpSelectionModel.getAll()];
})()"
# (8) Reload mesh → filter text preserved, selection cleared
browser-use eval "(() => {
  const inp = document.querySelector('#mp-patch-list .filter-input');
  inp.value = 'pump'; inp.dispatchEvent(new Event('input'));
  document.querySelector('#mp-load-mesh-btn').click();
  return 'reload-triggered';
})()"
sleep 5
browser-use eval "(() => ({
  filterText: document.querySelector('#mp-patch-list .filter-input').value,
  selection: [...mpSelectionModel.getAll()],
}))()"
browser-use screenshot /tmp/smoke-8-reload.png
# (9) Repeat for pp
browser-use eval "document.querySelector('[data-tab=\"postprocess\"]')?.click()"
browser-use eval "typeof ppInitPostProcess === 'function' ? ppInitPostProcess() : 'n/a'"
sleep 4
browser-use eval "(() => {
  const inp = document.querySelector('#pp-tree .filter-input');
  if (!inp) return 'no pp bar';
  inp.value = 'acu'; inp.dispatchEvent(new Event('input'));
  const r = document.querySelector('#pp-tree [data-patch]:not(.filter-hidden)');
  r?.click();
  return [...ppSelectionModel.getAll()];
})()"
browser-use screenshot /tmp/smoke-9-pp.png
browser-use close
```

Read each screenshot with the Read tool and confirm:
- `/tmp/smoke-1-filter-wall.png`: filter bar visible, count shows "N / M", rows filtered
- `/tmp/smoke-2-regex-fan.png`: `.*` button highlighted, only `^fan_` rows visible
- `/tmp/smoke-3-single-select.png`: one row has purple left border + background; 3D geometry tinted
- `/tmp/smoke-4-shift-range.png`: five contiguous rows purple; 3D shows 5 purple patches
- `/tmp/smoke-5-batch-visibility.png`: all five selected checkboxes unchecked together
- `/tmp/smoke-6-3d-click.png`: row scrolled into view + purple
- `/tmp/smoke-8-reload.png`: filter text still shows "pump", no row-selected present
- `/tmp/smoke-9-pp.png`: same visuals in pp panel

- [ ] **Step 3: Tear down server + submodule commit**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
```

The submodule should already have 8 commits on top of the pre-plan HEAD (Tasks 1-8). Verify:
```bash
cd STLGeneratorForOpenFoam
git log --oneline -10
```

Expected output ends with the Task 8 commit; no uncommitted changes in the submodule.

- [ ] **Step 4: Parent repo submodule pointer commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git status --short
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — patch-list filter + selection system

Adds substring/regex filter bar and 3D-linked multi-selection to both
mesh-preview and post-processing patch-list panels.

Spec: docs/superpowers/specs/2026-04-18-patch-list-filter-selection-design.md
Plan: docs/superpowers/plans/2026-04-18-patch-list-filter-selection.md
"
```

- [ ] **Step 5: Final regression run**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest \
  STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py \
  STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py \
  src/backend/aircooling/runner/tests/ 2>&1 | tail -5
cd STLGeneratorForOpenFoam
node --test scripts/test_patch_filter.mjs scripts/test_patch_selection.mjs 2>&1 | tail -5
```

Expected: all patch-list tests pass; runner tests same 4 pre-existing failures as baseline.

---

## Self-Review Notes

- **Spec coverage:** filter semantics (§3.2) → Task 1; selection model (§4) → Task 2; CSS (§8) + helpers → Task 3; glue pattern (§7) → Tasks 4-5; highlight (§5) → Task 3 helpers invoked from Tasks 4-5; 3D raycaster (§6) → Tasks 6-7; testing (§9) → Tasks 1-2 (unit) + Task 8 (structural) + Task 9 (smoke).
- **Placeholder scan:** no "TBD"/"TODO" strings; every code block is complete; every command is runnable.
- **Type consistency:** `mpSelectionModel.handleRowClick`, `handleCheckboxClick`, `setSelection`, `clear`, `getAll`, `has`, `getLastAnchor` — signatures match across Tasks 2, 4, 5, 6, 7. `applyFilterToDOM(container, predicate, filterBar)` — same 3-arg signature used in Tasks 3, 4, 5, 6, 7. `setPatchHighlight(mesh, on)` — same 2-arg used in Task 3 helpers and invoked from Tasks 4, 5.
- **Risks acknowledged:** existing renderer variable names (`mpRenderer`, `ppRenderer`, etc.) must be confirmed in Tasks 6-7 Step 1 — adapt if different.
