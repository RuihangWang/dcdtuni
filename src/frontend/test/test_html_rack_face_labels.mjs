// A rack's sides are named by the air they pass, in the legacy editor too.
//
// The user's report (Tri_angle, Default_Rack_1 U18): the right panel said
// "back" for the outlet side and "front" for the inlet. Both editors now name
// a rack side, U-slot side, server face and blanking-panel side through ONE
// shared helper, `rackState.rackFaceLabel` (shared/js/rack_state.js): front =
// 进风面 / Inlet, back = 出风面 / Outlet. The side keys stay the data.
//
// This pins the legacy's thin layer over it: `getRackSideLabel` — used by the
// U-slot panel, the multi-slot panel and the half-open hint — goes through the
// helper, and the hardcoded 'Front'/'Back' and English face tables are gone.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createContext, runInContext } from 'node:vm';

const require = createRequire(import.meta.url);
const rackState = require('../shared/js/rack_state.js');
const zh = JSON.parse(readFileSync(new URL('../shared/i18n/zh-CN.json', import.meta.url), 'utf8'));
const en = JSON.parse(readFileSync(new URL('../shared/i18n/en-US.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../web/STL_generate.html', import.meta.url), 'utf8');

const between = (start, end) => {
  const at = html.indexOf(start);
  assert.ok(at > 0, `${start} not found`);
  return html.slice(at, html.indexOf(end, at));
};

test('the shared helper maps the rack sides to their airflow roles', () => {
  assert.equal(rackState.rackFaceRole('front'), 'inlet');
  assert.equal(rackState.rackFaceRole('back'), 'outlet');
  assert.equal(rackState.rackFaceRole('top'), 'top');
  assert.equal(rackState.rackFaceLabel('back'), 'Outlet');
  assert.equal(rackState.rackFaceLabel('front', (k) => zh[k]), '进风面 (Inlet)');
  assert.equal(rackState.rackFaceLabel('back', (k) => en[k]), 'Outlet');
});

test('every key the helper names exists in both dictionaries', () => {
  for (const face of ['front', 'back', 'inlet', 'outlet', 'top', 'bottom', 'left', 'right']) {
    for (const key of [rackState.rackFaceLabelKey(face), rackState.rackFaceColorLabelKey(face)]) {
      if (!key) continue;
      assert.ok(zh[key], `zh-CN ${key}`);
      assert.ok(en[key], `en-US ${key}`);
    }
  }
});

test('the legacy getRackSideLabel goes through the shared helper', () => {
  const code = between('    function getRackFaceLabel(faceId) {', '    // ── V4 server access');
  for (const [dict, inlet, outlet] of [[zh, '进风面 (Inlet)', '出风面 (Outlet)'], [en, 'Inlet', 'Outlet']]) {
    const ctx = createContext({ window: { rackState }, t: (k) => dict[k] ?? k });
    runInContext(code + '\n;this.getRackSideLabel = getRackSideLabel; this.getRackFaceLabel = getRackFaceLabel;', ctx);
    assert.equal(ctx.getRackSideLabel('front'), inlet);
    assert.equal(ctx.getRackSideLabel('back'), outlet);
    assert.equal(ctx.getRackFaceLabel('outlet'), outlet);
  }
});

test('no legacy label site still prints the raw side or a hardcoded English face name', () => {
  assert.doesNotMatch(html, /side === 'back' \? 'Back' : 'Front'/);
  assert.doesNotMatch(html, /_faceLabels/);
  assert.doesNotMatch(html, /var faceLabels = \{ inlet: 'Inlet'/);
  assert.doesNotMatch(html, /◆ Inlet \(\$\{t\('label\.front_face'\)\}\)/);
  assert.doesNotMatch(html, /<label>Face Color<\/label>/);
  // The whole-server colour pickers name the two faces by role.
  const equipment = between("<div class=\"prop-title\">Naming & Color</div>", 'STL Region');
  assert.match(equipment, /t\('label\.inlet_color'\)/);
  assert.match(equipment, /t\('label\.outlet_color'\)/);
  assert.doesNotMatch(equipment, /label\.front_color|label\.back_color/);
});

test('the tree names the two rack face rows and the doors by role', () => {
  const tree = between("            ['front', 'back'].forEach(faceId => {", "} else if (model.kind === 'wall'");
  assert.match(tree, /const faceLabel = getRackFaceLabel\(faceId\);/);
  assert.match(tree, /t\(_doorSide === 'inlet' \? 'rack\.inlet_door' : 'rack\.outlet_door'\)/);
});
