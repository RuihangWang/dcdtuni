// The legacy editor refuses a cfdproj v5 project.
//
// v5 moves every time-varying boundary condition into `transient.tracks` and
// saves the fields stripped. STL_generate.html has no timeline: migrating such
// a file to V4 would load servers without power and ACUs without supply
// temperature. `_applyProjectData` therefore refuses anything above V4 before
// it pushes an undo checkpoint or clears the scene, and points to the React
// editor.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';

const zh = JSON.parse(readFileSync(new URL('../shared/i18n/zh-CN.json', import.meta.url), 'utf8'));
const en = JSON.parse(readFileSync(new URL('../shared/i18n/en-US.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../web/STL_generate.html', import.meta.url), 'utf8');

// Only the function head, up to its first try: the guard must sit there.
const start = html.indexOf('window._applyProjectData = function');
assert.ok(start > 0, '_applyProjectData not found');
const head = html.slice(start, html.indexOf('\n        try {', start));

function loader() {
  const alerts = [];
  const ctx = createContext({ window: {}, alert: (m) => alerts.push(m), t: (k) => zh[k] });
  runInContext(head + '\n};', ctx);
  return { apply: ctx.window._applyProjectData, alerts };
}

test('a v5 project is refused with a pointer to the React editor', () => {
  const { apply, alerts } = loader();
  assert.equal(apply({ schemaVersion: 5, transient: { tracks: [] } }, 'x.cfdproj'), false);
  assert.deepEqual(alerts, ['此工程为 cfdproj v5(含瞬态关键帧),请用 React 编辑器打开']);
});

test('v4 and older projects pass the guard', () => {
  const { apply, alerts } = loader();
  for (const v of [undefined, 2, 3, 4]) assert.equal(apply({ schemaVersion: v }, 'x.cfdproj'), undefined);
  assert.deepEqual(alerts, []);
});

test('the message exists in both dictionaries', () => {
  assert.ok(zh['error.project_v5_react_only']);
  assert.ok(en['error.project_v5_react_only']);
});
