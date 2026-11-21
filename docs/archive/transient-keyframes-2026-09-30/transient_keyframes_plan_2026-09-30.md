# Transient Keyframes (E7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Steady/transient timeline with per-parameter keyframe tracks as the only store of time-variable boundary conditions, compiled to `.liquidrun` v4 (DAE) and `cfdrun` v3 (OpenFOAM), with `cfdproj` v5 and lossless migration from v4.

**Architecture:** A shared JSON parameter registry names every keyframable field by a path template. A UMD module (`cfdproj_transient.js`, window + CommonJS, like the other `.cfdproj` modules) owns evaluation, projection (tracks → fields at time t), edit commit (field diff → keyframes), and v4→v5 migration; a Python twin mirrors it with a node parity test. The React store keeps the project materialised at the playhead; disk holds only tracks.

**Tech Stack:** plain JS (UMD) + node:test; TypeScript/React + vitest; Python 3.10 + pytest; later stages Pydantic, PyTorch DAE, OpenFOAM v2412.

**Spec:** `docs/developer/transient_keyframes_design_2026-09-30.md` (read §0–§1 before T1).

## Global Constraints

- Keyframe semantics: step by default; keyframe `(t, value, rampS)` ramps linearly over `[t − rampS, t]`; right-continuous (at `t` the new value holds).
- Every track has a `t = 0` keyframe with `rampS = 0`; keyframes strictly increasing in `t`; `0 ≤ rampS ≤ t_k − t_{k−1}`; `t_k` and `t_k − rampS` on the `gridS` grid; `t ≤ durationS`.
- Track identity: `(modelUid, param, target)`; `target` = bound path variables joined by `/` (empty string when none).
- Disk format: `schemaVersion: 5`, top-level `transient` block; registry fields are absent from stored models (only in tracks).
- Old v4 steady projects must open with identical field values at `t = 0` (regression over all `case/**/*.cfdproj`).
- Legacy HTML page stays v4-only.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; stage merges to `main` only after the stage gate.

## File Structure (T1)

| File | Responsibility |
|---|---|
| Create `src/frontend/shared/transient/transient_params.json` | The registry (data only) |
| Create `src/frontend/shared/js/cfdproj_transient.js` | UMD: registry access, path resolver, `valueAt`, `cornerTable`, `validateTransient`, `enumerateTargets`, `tracksFromFields`, `materializeAt`, `stripTrackedFields`, `commitEdit`, `migrateCfdprojV4toV5`, `migrateCfdprojToV5`, `defaultTransient` |
| Create `src/frontend/test/test_cfdproj_transient.mjs` | node:test unit tests for the module |
| Create `src/frontend/scripts/cfdproj_transient.py` | Python twin (same function set, snake_case) |
| Create `src/frontend/test/test_cfdproj_transient_py.py` | Python unit tests |
| Create `src/frontend/test/test_cfdproj_transient_parity.py` | JS↔Python migration parity + t=0 regression over all cases |
| Modify `src/frontend/shared/js/cfdproj_schema.js` | `SCHEMA_VERSION = 5`, `emptyDocument().transient` |
| Modify `src/frontend/react/src/legacy/cfdprojModules.ts` | import the new module; typed wrappers |
| Create `src/frontend/react/src/transient/model.ts` | typed facade over `window.cfdprojTransient` |
| Modify `src/frontend/react/src/schema/cfdproj.ts` | `transient?: TransientBlock` types |
| Modify `src/frontend/react/src/io/projectFiles.ts` | accept ≤5, migrate to v5, `SAVE_KEYS += transient`, strip on save |
| Modify `src/frontend/react/src/projectStore.ts` | playhead state, materialise on load/undo/redo/playhead, `commitEdit` in `edit()` |
| Modify `src/server/server.py`, `src/backend/aircooling/runner/hybrid_air_heat_handoff.py` | accept v5 via Python projection at t=0 |

---

## Stage T1 — core model

> **T1 done (2026-09-30):** gate passed, receipt `docs/benchmarks/transient-keyframes-t1-2026-09-30/`. Deviations: static = scene state only (geometry + constructions; spec §1.5); `projcomp` refuses v5 templates; `SCHEMA_VERSION` in `cfdproj_schema.js` stays 4 (the v3→v4 migration stamps it), `validateDocument` accepts 4 and 5.

### Task 1: Registry + path resolver

**Files:**
- Create: `src/frontend/shared/transient/transient_params.json`
- Create: `src/frontend/shared/js/cfdproj_transient.js`
- Test: `src/frontend/test/test_cfdproj_transient.mjs`

**Interfaces:**
- Produces: `T.registry()` → `{format, params: Entry[]}`; `Entry = {key, mode, bucket, path, unit, min, max, default?, createWithDefault?, where?}`; `T.enumerateTargets(model, entry)` → `[{target: string, value: any}]` (only present values unless `createWithDefault`); `T.getAt(model, entry, target)`; `T.setAt(model, entry, target, value)`; `T.deleteAt(model, entry, target)`.

Path grammar: segments split on `.`; `{var}` binds an object key or array index; `seg[id={var}]` selects the array element of `seg` whose `id` equals the bound value; `*` iterates all keys of an object without binding. `where` is evaluated on the object selected by the last binding selector (`{var}` or `[id=…]`): each key is a dotted sub-path, the value either a literal or an array of allowed literals.

- [x] **Step 1: Write the registry**

```json
{
  "format": "dcdtuni.transient-params/v1",
  "params": [
    {"key": "server.power", "mode": "air", "bucket": "racks", "path": "constructions.servers.{server}.cooling.power", "unit": "W", "min": 0, "max": 1000000},
    {"key": "box.heat", "mode": "air", "bucket": "boxes", "path": "cooling.totalPower", "unit": "W", "min": 0, "max": 10000000},
    {"key": "acu.supplyTemp", "mode": "air", "bucket": "acus", "path": "geometry.faces.*.patches[id={patch}].physics.temperature", "unit": "degC", "min": -20, "max": 80, "where": {"physics.role": "supply"}},
    {"key": "acu.airflow", "mode": "air", "bucket": "acus", "path": "geometry.faces.*.patches[id={patch}].physics.volumeFlowRate", "unit": "m3/s", "min": 0, "max": 100, "where": {"physics.role": "supply"}},
    {"key": "acu.supplyAngle", "mode": "air", "bucket": "acus", "path": "geometry.faces.*.patches[id={patch}].physics.supplyAngleDeg", "unit": "deg", "min": -60, "max": 60, "where": {"physics.role": "supply"}},
    {"key": "cp.heat", "mode": "liquid", "bucket": "racks", "path": "constructions.servers.{server}.liquidCooling.cpCuPairs.{pair}.Q_W", "unit": "W", "min": 0, "max": 100000},
    {"key": "cp.dT", "mode": "liquid", "bucket": "racks", "path": "constructions.servers.{server}.liquidCooling.cpCuPairs.{pair}.dT_K", "unit": "K", "min": 0, "max": 100},
    {"key": "pump.rpm", "mode": "liquid", "bucket": "cdus", "path": "constructions.pump.rpmRatio", "unit": "1", "min": 0, "max": 2, "default": 1, "createWithDefault": true},
    {"key": "pump.flow", "mode": "liquid", "bucket": "cdus", "path": "cooling.totalFlowRate", "unit": "L/min", "min": 0, "max": 100000},
    {"key": "valve.opening", "mode": "liquid", "bucket": "pipeSegments", "path": "cooling.valves[id={valve}].opening", "unit": "1", "min": 0.001, "max": 1, "where": {"type": ["flow_control", "isolation"]}},
    {"key": "cdu.primaryInletT", "mode": "liquid", "bucket": "cdus", "path": "cooling.primaryInletTemperatureC", "unit": "degC", "min": 0, "max": 80, "default": 20, "createWithDefault": true},
    {"key": "cdu.primaryFlow", "mode": "liquid", "bucket": "cdus", "path": "cooling.primaryFlowKgS", "unit": "kg/s", "min": 0, "max": 1000, "default": 1, "createWithDefault": true},
    {"key": "cdu.secondarySupplyT", "mode": "liquid", "bucket": "cdus", "path": "cooling.supplyTemperatureC", "unit": "degC", "min": 0, "max": 80},
    {"key": "cdu.returnPressure", "mode": "liquid", "bucket": "cdus", "path": "cooling.returnPressureKpa", "unit": "kPa", "min": 1, "max": 2000, "default": 150, "createWithDefault": true}
  ]
}
```

- [x] **Step 2: Write the failing resolver tests** (`test_cfdproj_transient.mjs`)

```js
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
});
test('get/set/delete by target', () => {
  const m = acu();
  assert.equal(T.getAt(m, entry('acu.supplyTemp'), 'p_1'), 20);
  T.setAt(m, entry('acu.supplyTemp'), 'p_1', 18);
  assert.equal(m.geometry.faces.front.patches[0].physics.temperature, 18);
  T.deleteAt(m, entry('acu.supplyTemp'), 'p_1');
  assert.equal('temperature' in m.geometry.faces.front.patches[0].physics, false);
  const r = rack(); T.setAt(r, entry('cp.heat'), 'e_1/1', 75);
  assert.equal(r.constructions.servers.e_1.liquidCooling.cpCuPairs[1].Q_W, 75);
});
```

- [x] **Step 3: Run to verify failure**

Run: `cd src/frontend && node --test test/test_cfdproj_transient.mjs`
Expected: FAIL (`Cannot find module '../shared/js/cfdproj_transient.js'`).

- [x] **Step 4: Implement the module skeleton + resolver**

```js
(function () {
  'use strict';
  let REGISTRY = null;
  function registry() {
    if (REGISTRY) return REGISTRY;
    if (typeof window !== 'undefined' && window.__TRANSIENT_PARAMS__) REGISTRY = window.__TRANSIENT_PARAMS__;
    else REGISTRY = require('../transient/transient_params.json');
    return REGISTRY;
  }
  function setRegistry(r) { REGISTRY = r; }  // browser bundlers inject the JSON

  function parse(path) {
    return path.split('.').map((seg) => {
      let m = /^\{(\w+)\}$/.exec(seg);
      if (m) return { kind: 'var', name: m[1] };
      m = /^(\w+)\[id=\{(\w+)\}\]$/.exec(seg);
      if (m) return { kind: 'idsel', key: m[1], name: m[2] };
      if (seg === '*') return { kind: 'any' };
      return { kind: 'key', key: seg };
    });
  }
  function dig(obj, dotted) {
    return dotted.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }
  function matches(obj, where) {
    if (!where) return true;
    return Object.entries(where).every(([k, want]) => {
      const v = dig(obj, k);
      return Array.isArray(want) ? want.includes(v) : v === want;
    });
  }
  // Walk the template; call leaf(parent, key, bindings, selected) for every concrete leaf.
  function walk(obj, segs, i, bindings, selected, where, leaf) {
    if (obj == null || typeof obj !== 'object') return;
    const s = segs[i];
    const last = i === segs.length - 1;
    if (s.kind === 'key') {
      if (last) { if (matches(selected, where)) leaf(obj, s.key, bindings); return; }
      walk(obj[s.key], segs, i + 1, bindings, selected, where, leaf);
    } else if (s.kind === 'any') {
      for (const k of Object.keys(obj)) walk(obj[k], segs, i + 1, bindings, selected, where, leaf);
    } else if (s.kind === 'var') {
      const keys = Array.isArray(obj) ? obj.map((_, j) => j) : Object.keys(obj);
      for (const k of keys) walk(obj[k], segs, i + 1, bindings.concat(String(k)), obj[k], where, leaf);
    } else {
      const arr = obj[s.key];
      if (!Array.isArray(arr)) return;
      for (const el of arr) if (el && el.id != null) walk(el, segs, i + 1, bindings.concat(String(el.id)), el, where, leaf);
    }
  }
  function enumerateTargets(model, entry) {
    const out = [];
    const segs = parse(entry.path);
    walk(model, segs, 0, [], model, entry.where, (parent, key, bindings) => {
      const value = parent[key];
      if (value !== undefined && value !== null) out.push({ target: bindings.join('/'), value });
      else if (entry.createWithDefault) out.push({ target: bindings.join('/'), value: entry.default });
    });
    if (!out.length && entry.createWithDefault && !segs.some(s => s.kind !== 'key')) out.push({ target: '', value: entry.default });
    return out;
  }
  // Resolve a concrete parent/key for (entry, target); create plain-key parents when `create`.
  function locate(model, entry, target, create) {
    const segs = parse(entry.path);
    const vals = target === '' ? [] : target.split('/');
    let obj = model, vi = 0;
    for (let i = 0; i < segs.length - 1; i++) {
      const s = segs[i];
      if (obj == null) return null;
      if (s.kind === 'key') {
        if (obj[s.key] == null) { if (!create) return null; obj[s.key] = {}; }
        obj = obj[s.key];
      } else if (s.kind === 'var') {
        const k = vals[vi++]; obj = Array.isArray(obj) ? obj[Number(k)] : obj[k];
      } else if (s.kind === 'idsel') {
        const id = vals[vi++]; obj = (obj[s.key] || []).find(el => el && String(el.id) === id);
      } else {
        const rest = { ...entry, path: segs.slice(i + 1).map(fmt).join('.') };
        for (const k of Object.keys(obj)) {
          const hit = locate(obj[k], rest, vals.slice(vi).join('/'), create);
          if (hit) return hit;
        }
        return null;
      }
    }
    const lastSeg = segs[segs.length - 1];
    return obj == null ? null : { parent: obj, key: lastSeg.key };
  }
  function fmt(s) {
    return s.kind === 'key' ? s.key : s.kind === 'var' ? `{${s.name}}` : s.kind === 'any' ? '*' : `${s.key}[id={${s.name}}]`;
  }
  function getAt(model, entry, target) { const h = locate(model, entry, target, false); return h ? h.parent[h.key] : undefined; }
  function setAt(model, entry, target, value) {
    const h = locate(model, entry, target, true);
    if (!h) throw new Error(`transient: no field for ${entry.key} target "${target}"`);
    h.parent[h.key] = value;
  }
  function deleteAt(model, entry, target) { const h = locate(model, entry, target, false); if (h) delete h.parent[h.key]; }

  const api = { registry, setRegistry, enumerateTargets, getAt, setAt, deleteAt };
  if (typeof window !== 'undefined') window.cfdprojTransient = Object.assign(window.cfdprojTransient || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
```

- [x] **Step 5: Run tests** — `node --test test/test_cfdproj_transient.mjs` → PASS.
- [x] **Step 6: Commit** — `git add src/frontend/shared/transient src/frontend/shared/js/cfdproj_transient.js src/frontend/test/test_cfdproj_transient.mjs && git commit -m "transient(T1.1): parameter registry and path resolver"`

### Task 2: Evaluator, corner table, validation

**Files:** Modify `cfdproj_transient.js`; Test `test_cfdproj_transient.mjs`.

**Interfaces:**
- Produces: `T.valueAt(keyframes, t) → number`; `T.cornerTable(keyframes, durationS) → [[t, v], …]` (a step is two rows at the same `t`); `T.validateTransient(transient) → [{path, msg}]`; `T.defaultTransient(doc) → Transient`.

- [x] **Step 1: Failing tests**

```js
const kf = [{ t: 0, value: 10 }, { t: 60, value: 20 }, { t: 120, value: 5, rampS: 30 }];
test('step then ramp, right-continuous', () => {
  assert.equal(T.valueAt(kf, 0), 10); assert.equal(T.valueAt(kf, 59.9), 10);
  assert.equal(T.valueAt(kf, 60), 20); assert.equal(T.valueAt(kf, 90), 20);
  assert.equal(T.valueAt(kf, 105), 12.5); assert.equal(T.valueAt(kf, 120), 5); assert.equal(T.valueAt(kf, 500), 5);
});
test('corner table', () => {
  assert.deepEqual(T.cornerTable(kf, 200), [[0, 10], [60, 10], [60, 20], [90, 20], [120, 5], [200, 5]]);
});
test('validation catches ordering, ramps, grid, bounds', () => {
  const ok = { mode: 'transient', durationS: 200, gridS: 1, start: { kind: 'uniform', uniform: {} },
    tracks: [{ modelUid: 'r1', param: 'server.power', target: 'e_1', keyframes: kf }] };
  assert.deepEqual(T.validateTransient(ok), []);
  const bad = structuredClone(ok);
  bad.tracks[0].keyframes = [{ t: 0, value: 10 }, { t: 60.5, value: 20 }, { t: 50, value: 1, rampS: 90 }];
  const msgs = T.validateTransient(bad).map(e => e.msg).join(' | ');
  assert.match(msgs, /grid/); assert.match(msgs, /increasing/);
  const noZero = structuredClone(ok); noZero.tracks[0].keyframes = [{ t: 10, value: 1 }];
  assert.match(T.validateTransient(noZero)[0].msg, /t = 0/);
});
```

- [x] **Step 2: Run → FAIL** (`T.valueAt is not a function`).
- [x] **Step 3: Implement**

```js
  const EPS = 1e-9;
  function valueAt(kfs, t) {
    let prev = kfs[0].value;
    for (let k = 1; k < kfs.length; k++) {
      const { t: tk, value: vk } = kfs[k]; const r = kfs[k].rampS || 0;
      if (t >= tk - EPS) { prev = vk; continue; }
      if (r > 0 && t >= tk - r) return prev + (vk - prev) * (t - (tk - r)) / r;
      return prev;
    }
    return prev;
  }
  function cornerTable(kfs, durationS) {
    const rows = [[0, kfs[0].value]];
    for (let k = 1; k < kfs.length; k++) {
      const r = kfs[k].rampS || 0;
      rows.push([kfs[k].t - r, kfs[k - 1].value], [kfs[k].t, kfs[k].value]);
    }
    const last = rows[rows.length - 1];
    if (durationS > last[0]) rows.push([durationS, last[1]]);
    return rows.filter((row, i) => i === 0 || row[0] !== rows[i - 1][0] || row[1] !== rows[i - 1][1]);
  }
  function onGrid(x, g) { return Math.abs(x / g - Math.round(x / g)) < 1e-9 * Math.max(1, Math.abs(x / g)); }
  function validateTransient(tr) {
    const errs = []; const reg = registry();
    const push = (path, msg) => errs.push({ path, msg });
    if (!tr || typeof tr !== 'object') return [{ path: 'transient', msg: 'missing transient block' }];
    if (tr.mode !== 'steady' && tr.mode !== 'transient') push('transient.mode', 'mode must be steady or transient');
    if (!(tr.durationS > 0)) push('transient.durationS', 'duration must be > 0');
    if (!(tr.gridS > 0)) push('transient.gridS', 'grid must be > 0');
    if (!tr.start || !['steadyConverged', 'uniform'].includes(tr.start.kind)) push('transient.start', 'start must be steadyConverged or uniform');
    const seen = new Set();
    (tr.tracks || []).forEach((trk, i) => {
      const p = `transient.tracks[${i}]`;
      const entry = reg.params.find(e => e.key === trk.param);
      if (!entry) { push(p, `unknown parameter ${trk.param}`); return; }
      const id = `${trk.modelUid}|${trk.param}|${trk.target}`;
      if (seen.has(id)) push(p, 'duplicate track'); seen.add(id);
      const k = trk.keyframes || [];
      if (!k.length || k[0].t !== 0 || (k[0].rampS || 0) !== 0) push(p, 'a track needs a keyframe at t = 0 without ramp');
      k.forEach((f, j) => {
        const q = `${p}.keyframes[${j}]`;
        if (!Number.isFinite(f.value) || f.value < entry.min || f.value > entry.max) push(q, `value outside [${entry.min}, ${entry.max}]`);
        if (j === 0) return;
        const r = f.rampS || 0;
        if (!(f.t > k[j - 1].t)) push(q, 'keyframe times must be strictly increasing');
        if (r < 0 || r > f.t - k[j - 1].t + EPS) push(q, 'ramp longer than the gap to the previous keyframe');
        if (tr.gridS > 0 && (!onGrid(f.t, tr.gridS) || !onGrid(f.t - r, tr.gridS))) push(q, `time not on the ${tr.gridS} s grid`);
        if (f.t > tr.durationS + EPS) push(q, 'keyframe after the end of the timeline');
      });
    });
    return errs;
  }
  function defaultTransient(doc) {
    const roomT = Number((doc && doc.geometry && doc.geometry.temperature) ?? 22);
    return { mode: 'steady', durationS: 600, gridS: 1,
      start: { kind: 'uniform', uniform: { airT_C: roomT, waterT_C: 30, solidT_C: 40 } }, tracks: [] };
  }
```
Add `valueAt, cornerTable, validateTransient, defaultTransient` to `api`.

- [x] **Step 4: Run → PASS.** **Step 5: Commit** `transient(T1.2): evaluator, corner table, validation`.

### Task 3: Materialise, strip, tracks-from-fields, edit commit

**Files:** Modify `cfdproj_transient.js`; Test `test_cfdproj_transient.mjs`.

**Interfaces:**
- Produces: `T.forEachModel(doc, fn(bucket, uid, model))`; `T.tracksFromFields(doc) → Track[]` (t=0 keyframes from present fields/defaults); `T.materializeAt(doc, t)` (in place: every track's field := `valueAt(track, t)`); `T.stripTrackedFields(doc)` (in place: delete every tracked field); `T.commitEdit(before, draft, t) → {ok: true} | {ok: false, reason: 'structure'|'static', detail}` (mutates `draft.transient.tracks`).

- [x] **Step 1: Failing tests**

```js
const doc = () => ({ schemaVersion: 5, geometry: { temperature: 22 }, constructions: { racks: { r1: rack() }, acus: { a1: acu() } },
  transient: { mode: 'transient', durationS: 600, gridS: 1, start: { kind: 'uniform', uniform: {} }, tracks: [] } });
test('tracks from fields, strip, materialise round-trip', () => {
  const d = doc(); d.transient.tracks = T.tracksFromFields(d);
  assert.equal(d.transient.tracks.length, 2 + 2 + 2); // server.power×2, cp.heat×2, acu temp+flow
  const original = structuredClone(d);
  T.stripTrackedFields(d);
  assert.equal(d.constructions.racks.r1.constructions.servers.e_1.cooling.power, undefined);
  T.materializeAt(d, 0);
  assert.deepEqual(d.constructions, original.constructions);
});
test('commitEdit writes a keyframe at the playhead and rejects static edits off t=0', () => {
  const d = doc(); d.transient.tracks = T.tracksFromFields(d); T.materializeAt(d, 120);
  const draft = structuredClone(d);
  draft.constructions.racks.r1.constructions.servers.e_1.cooling.power = 1500;
  assert.deepEqual(T.commitEdit(d, draft, 120), { ok: true });
  const trk = draft.transient.tracks.find(x => x.param === 'server.power' && x.target === 'e_1');
  assert.deepEqual(trk.keyframes, [{ t: 0, value: 1000 }, { t: 120, value: 1500, rampS: 0 }]);
  const geo = structuredClone(d); geo.constructions.acus.a1.geometry.faces.front.name = 'moved';
  assert.equal(T.commitEdit(d, geo, 120).reason, 'static');
  const struct = structuredClone(d); delete struct.constructions.racks.r1.constructions.servers.e_2;
  assert.equal(T.commitEdit(d, struct, 120).reason, 'structure');
});
test('commitEdit at t=0 reconciles structure', () => {
  const d = doc(); d.transient.tracks = T.tracksFromFields(d); T.materializeAt(d, 0);
  const draft = structuredClone(d);
  delete draft.constructions.racks.r1.constructions.servers.e_2;
  draft.constructions.racks.r1.constructions.servers.e_3 = { cooling: { power: 700 } };
  assert.deepEqual(T.commitEdit(d, draft, 0), { ok: true });
  const targets = draft.transient.tracks.filter(x => x.param === 'server.power').map(x => x.target).sort();
  assert.deepEqual(targets, ['e_1', 'e_3']);
});
test('inserting a keyframe clamps the next ramp', () => {
  const d = doc(); d.transient.tracks = [{ modelUid: 'r1', param: 'server.power', target: 'e_1',
    keyframes: [{ t: 0, value: 1000 }, { t: 100, value: 2000, rampS: 100 }] }];
  T.materializeAt(d, 60);
  const draft = structuredClone(d); draft.constructions.racks.r1.constructions.servers.e_1.cooling.power = 900;
  T.commitEdit(d, draft, 60);
  assert.deepEqual(draft.transient.tracks[0].keyframes, [{ t: 0, value: 1000 }, { t: 60, value: 900, rampS: 0 }, { t: 100, value: 2000, rampS: 40 }]);
});
```

- [x] **Step 2: Run → FAIL.**
- [x] **Step 3: Implement**

```js
  function forEachModel(doc, fn) {
    for (const [bucket, items] of Object.entries((doc && doc.constructions) || {})) {
      if (!items || typeof items !== 'object') continue;
      for (const [uid, model] of Object.entries(items)) if (model && typeof model === 'object') fn(bucket, uid, model);
    }
  }
  function entriesFor(bucket) { return registry().params.filter(e => e.bucket === bucket); }
  function snapshotTargets(doc) {
    const out = new Map();
    forEachModel(doc, (bucket, uid, model) => {
      for (const e of entriesFor(bucket)) for (const { target, value } of enumerateTargets(model, e))
        out.set(`${uid}|${e.key}|${target}`, { uid, entry: e, target, value });
    });
    return out;
  }
  function tracksFromFields(doc) {
    return [...snapshotTargets(doc).values()].map(({ uid, entry, target, value }) =>
      ({ modelUid: uid, param: entry.key, target, keyframes: [{ t: 0, value: Number(value) }] }));
  }
  function modelIndex(doc) { const m = new Map(); forEachModel(doc, (b, uid, model) => m.set(uid, { bucket: b, model })); return m; }
  function entryOf(key) { return registry().params.find(e => e.key === key); }
  function materializeAt(doc, t) {
    const idx = modelIndex(doc);
    for (const trk of ((doc.transient && doc.transient.tracks) || [])) {
      const hit = idx.get(trk.modelUid); const e = entryOf(trk.param);
      if (hit && e) setAt(hit.model, e, trk.target, valueAt(trk.keyframes, t));
    }
    return doc;
  }
  function stripTrackedFields(doc) {
    const idx = modelIndex(doc);
    for (const trk of ((doc.transient && doc.transient.tracks) || [])) {
      const hit = idx.get(trk.modelUid); const e = entryOf(trk.param);
      if (hit && e) deleteAt(hit.model, e, trk.target);
    }
    return doc;
  }
  function withoutTracked(doc) {
    const copy = JSON.parse(JSON.stringify(doc));
    delete copy.transient;
    const tmp = { constructions: copy.constructions, transient: doc.transient };
    stripTrackedFields(tmp);
    return JSON.stringify(copy);
  }
  function upsert(trk, t, value) {
    const k = trk.keyframes; const i = k.findIndex(f => Math.abs(f.t - t) < EPS);
    if (i >= 0) { k[i] = i === 0 ? { t: 0, value } : { ...k[i], value }; return; }
    let at = k.findIndex(f => f.t > t); if (at < 0) at = k.length;
    k.splice(at, 0, { t, value, rampS: 0 });
    const next = k[at + 1];
    if (next && (next.rampS || 0) > next.t - t) next.rampS = next.t - t;
  }
  function commitEdit(before, draft, t) {
    const tr = draft.transient || (draft.transient = defaultTransient(draft));
    tr.tracks = tr.tracks || [];
    const a = snapshotTargets(before), b = snapshotTargets(draft);
    const added = [...b.keys()].filter(k => !a.has(k)), removed = [...a.keys()].filter(k => !b.has(k));
    if (t > EPS && (added.length || removed.length)) return { ok: false, reason: 'structure', detail: [...added, ...removed].slice(0, 5) };
    if (t > EPS && withoutTracked(before) !== withoutTracked(draft)) return { ok: false, reason: 'static', detail: [] };
    const find = (key) => { const [uid, param, target] = key.split('|'); return tr.tracks.find(x => x.modelUid === uid && x.param === param && x.target === target); };
    for (const key of removed) { const trk = find(key); if (trk) tr.tracks.splice(tr.tracks.indexOf(trk), 1); }
    for (const key of added) { const s = b.get(key); tr.tracks.push({ modelUid: s.uid, param: s.entry.key, target: s.target, keyframes: [{ t: 0, value: Number(s.value) }] }); }
    for (const [key, s] of b) {
      const old = a.get(key); if (!old || Number(old.value) === Number(s.value)) continue;
      const trk = find(key); if (trk) upsert(trk, t, Number(s.value));
    }
    return { ok: true };
  }
```
Add all to `api`.

- [x] **Step 4: Run → PASS.** **Step 5: Commit** `transient(T1.3): materialise/strip/commitEdit`.

### Task 4: v4 → v5 migration (JS)

**Files:** Modify `cfdproj_transient.js` (requires `cfdproj_migrate.js` for the v≤4 chain); Test `test_cfdproj_transient.mjs`.

**Interfaces:**
- Produces: `T.migrateCfdprojV4toV5WithReport(raw) → {doc, report: string[]}`; `T.migrateCfdprojV4toV5(raw) → doc`; `T.migrateCfdprojToV5(raw) → doc` (v1–v4 → v5; v5 passes through); window aliases `window.migrateCfdprojToV5`.

Rules (spec §1.6): tracks from fields (t=0) → apply `timeVarying` conversions → apply liquid experiment conversions → pick `mode`, `durationS`, `start`, `gridS` → strip tracked fields → drop converted legacy keys → `schemaVersion = 5`.
- `timeVarying` mapping: ACU `airflow`/`supplyTemp` → `acu.airflow`/`acu.supplyTemp` on every supply patch; box `heatOutput` → `box.heat`; rack `power:<serverId>` → `server.power` target `<serverId>`; anything else → report "dropped".
- Point conversion (old series interpolated linearly between consecutive POINTS): sort points by `t`; the t=0 keyframe value = the first point's value (report if it differs from the field). For each later point `i`, with `gap = t_i − t_{i−1}` measured between points: if `v_i === v_{i−1}` → no keyframe (a hold); else if `gap ≤ 0.1 + 1e-9` (the editor's fake step) → keyframe `{t: t_{i−1}, value: v_i, rampS: 0}`; else → keyframe `{t: t_i, value: v_i, rampS: gap}`. Worked example: `[(0,20),(100,20),(100.1,16),(200,18)]` → `[{0,20},{100,16,0},{200,18,99.9}]`.
- `gridS` = the largest of `[1, 0.5, 0.1, 0.05, 0.01, 0.001]` dividing every keyframe time and ramp start (liquid `dt_s` also must divide it: if the liquid experiment is transient use `min(candidate, dt_s)` when `dt_s` divides it, else `dt_s`).
- Liquid experiment (`runDefaults.liquid.experiment`): `duration_s → durationS`; `settings.loadStep` true → every `cp.heat` track gets `{t: stepTime, value: base·loadFactor}`; `settings.supplySchedule` → `cdu.primaryInletT` track (fixed → t=0 value; step → keyframe; ramp → keyframe with `rampS`; sinusoid → keyframes every `gridS` with `rampS = gridS`, reported); remove `duration_s`, `settings.loadStep/stepTime/loadFactor/supplySchedule` from the experiment.
- `mode = 'transient'` if `simulationConfig.solverType === 'transient'` or `experiment.dae.analysis.mode === 'transient'`, else `'steady'`; `durationS` = liquid `duration_s` ?? air `endTimeSeconds` ?? 600; `start.kind` = liquid `dae.analysis.initialization === 'steady'` → `steadyConverged`, else `uniform` with `airT_C = geometry.temperature ?? 22`.

- [x] **Step 1: Failing tests** — steady v4 case round-trips (`materializeAt(migrate(v4), 0)` has the same `constructions` as the v4 input after `migrateCfdprojToV4`); a synthetic v4 with ACU `timeVarying.supplyTemp = [{t:0,value:20},{t:100,value:20},{t:100.1,value:16},{t:200,value:18}]` migrates to keyframes `[{t:0,value:20},{t:100,value:16,rampS:0},{t:200,value:18,rampS:99.9}]` with `gridS = 0.1`; a liquid experiment with `loadStep` at 60 s ×1.2 yields `cp.heat` keyframes `[{t:0,value:100},{t:60,value:120,rampS:0}]`; `schemaVersion === 5` and no `timeVarying` remains; v5 input passes through unchanged. (Write these as `test(...)` blocks with literal fixtures as in Tasks 1–3.)
- [x] **Step 2: Run → FAIL.** **Step 3: Implement** the rules above in `migrateCfdprojV4toV5WithReport`. **Step 4: Run → PASS.**
- [x] **Step 5: Regression over every case** — add a test that globs `case/**/*.cfdproj` (skip unreadable), runs `migrateCfdprojToV4` then `migrateCfdprojToV5`, materialises at 0, and asserts deep equality of `constructions` with the v4 result for projects whose models carry no `timeVarying`; for the rest assert the same after deleting `timeVarying`. Run → PASS.
- [x] **Step 6: Commit** `transient(T1.4): cfdproj v4→v5 migration with timeVarying and liquid experiment conversion`.

### Task 5: Python twin + parity

**Files:** Create `src/frontend/scripts/cfdproj_transient.py`, `src/frontend/test/test_cfdproj_transient_py.py`, `src/frontend/test/test_cfdproj_transient_parity.py`.

**Interfaces:**
- Produces: `registry()`, `enumerate_targets(model, entry)`, `get_at/set_at/delete_at`, `value_at(keyframes, t)`, `corner_table(keyframes, duration_s)`, `validate_transient(tr)`, `tracks_from_fields(doc)`, `materialize_at(doc, t)`, `strip_tracked_fields(doc)`, `project_at(doc, t) -> dict` (deep copy + materialise), `migrate_v4_to_v5(raw) -> dict`, `migrate_to_v5(raw) -> dict` (uses `cfdproj_migrate.migrate_to_v4` for older input).

- [x] **Step 1:** Port Tasks 1–4 line by line (same names in snake_case, same registry file path `src/frontend/shared/transient/transient_params.json`).
- [x] **Step 2:** `test_cfdproj_transient_py.py` repeats the Task 1–3 unit tests in pytest form.
- [x] **Step 3:** `test_cfdproj_transient_parity.py`: for every `case/**/*.cfdproj`, run the JS `migrateCfdprojToV5` via `node -e` (pattern of `test_cfdproj_migrate_v4_parity.py`) and the Python `migrate_to_v5`; compare canonical JSON (integer-valued floats → int). Also assert `project_at(doc, 0)` equals the JS `materializeAt(structuredClone(doc), 0)`.
- [x] **Step 4:** `cd src/frontend && python -m pytest test/test_cfdproj_transient_py.py test/test_cfdproj_transient_parity.py -q` → PASS.
- [x] **Step 5: Commit** `transient(T1.5): Python twin and JS parity`.

### Task 6: v5 load/save in React + schema constants

**Files:** Modify `shared/js/cfdproj_schema.js`, `react/src/legacy/cfdprojModules.ts`, `react/src/schema/cfdproj.ts`, `react/src/io/projectFiles.ts`; Create `react/src/transient/model.ts`; Tests `react/test/transientModel.test.ts`, existing `react/test/*projectFiles*`.

**Interfaces:**
- Produces (TS): `type Keyframe = {t: number; value: number; rampS?: number}`; `type Track = {modelUid: string; param: string; target: string; keyframes: Keyframe[]}`; `type TransientBlock = {mode: 'steady'|'transient'; durationS: number; gridS: number; start: {kind: 'steadyConverged'|'uniform'; uniform?: {airT_C?: number; waterT_C?: number; solidT_C?: number}}; tracks: Track[]}`; `transientModel.{valueAt, cornerTable, validateTransient, materializeAt, stripTrackedFields, commitEdit, tracksFromFields, migrateToV5, registry}`.

- [x] **Step 1:** `cfdproj_schema.js`: `SCHEMA_VERSION = 5`; `emptyDocument()` adds `transient: {mode:'steady', durationS:600, gridS:1, start:{kind:'uniform', uniform:{airT_C:22, waterT_C:30, solidT_C:40}}, tracks:[]}`; keep `validateDocument` accepting 4 and 5.
- [x] **Step 2:** `cfdprojModules.ts`: `import '../../../shared/js/cfdproj_transient.js'` after `cfdproj_migrate.js`, and inject the registry JSON: `import params from '../../../shared/transient/transient_params.json'; (window as any).__TRANSIENT_PARAMS__ = params` before the import (Vite resolves JSON). `transient/model.ts` wraps `window.cfdprojTransient` with the types above.
- [x] **Step 3: Failing vitest** (`transientModel.test.ts`): loading `case/Shinaide_Liquid/Shinaide_liquid.cfdproj` text through `readProjectFileText` yields `schemaVersion 5` and a `transient` block; `serializeProjectState(materialised)` contains no `"power": 1000` under servers and contains `"transient"`; a document with `schemaVersion: 6` is refused.
- [x] **Step 4:** `projectFiles.ts`: `windowMigrator()` → `window.migrateCfdprojToV5`; refuse `version > 5`; `SAVE_KEYS` += `'transient'`; `projectForSave` works on a deep clone: `stripTrackedFields(clone)`, `clone.schemaVersion = 5`, and adds `validateTransient` errors to the save-time error list.
- [x] **Step 5:** Legacy page guard: in `src/frontend/web/STL_generate.html`'s project loader (where it calls `migrateCfdprojToV4`), refuse `schemaVersion > 4` with the message "此工程为 cfdproj v5(含瞬态关键帧),请用 React 编辑器打开" before migrating; add a case to `src/frontend/test/test_html_case_path_restore.mjs` style harness if one loads projects, else a node test that greps the guard.
- [x] **Step 6:** `npx vitest run` → all pass; `npx tsc -b` clean. **Step 7: Commit** `transient(T1.6): React loads and saves cfdproj v5; legacy page refuses v5`.

### Task 7: Store integration (playhead, materialise, commitEdit)

**Files:** Modify `react/src/projectStore.ts`; Test `react/test/projectStoreTransient.test.ts`.

**Interfaces:**
- Produces: `playheadTime(): number`; `setPlayhead(t: number): void` (re-materialises, no undo, not dirty, emits); `subscribePlayhead(fn)`; `lastEditRejection(): {reason, detail} | null` (set when `edit()` refuses); `edit()` unchanged signature.

- [x] **Step 1: Failing tests:** after `loadProject(v5doc)` the store project has fields materialised at 0; `setPlayhead(120)` shows the 120 s value; `edit()` changing a server power at 120 s adds a keyframe and keeps undo working (undo restores the previous tracks and re-materialises at the current playhead); `edit()` moving a model at 120 s is refused (`lastEditRejection().reason === 'static'`) and leaves the project unchanged and not dirty.
- [x] **Step 2: Implement:**
  - `let playhead = 0`; `loadProject`: after `reviveKinds`, ensure `revived.transient` (default when absent) and `materializeAt(revived, 0)`; reset `playhead = 0`.
  - `edit()`: after `mutate(draft)`: `const r = commitEdit(before, draft, playhead)`; if `!r.ok` → set rejection, `logOperation('editRejected', r.reason)`, `emit()`, return without touching stacks; else `materializeAt(draft, playhead)` then the existing push/emit path.
  - `undo`/`redo`: after swapping, `state.project = materializeAt(cloneProject(prev), playhead)`.
  - `setPlayhead(t)`: clamp to `[0, durationS]`, snap to `gridS`; `state.project = materializeAt(cloneProject(state.project), t)`; notify playhead listeners; `emit()`.
- [x] **Step 3:** vitest → PASS (and full suite green). **Step 4: Commit** `transient(T1.7): store materialises at the playhead and commits edits as keyframes`.

### Task 8: Python consumers accept v5

**Files:** Modify `src/server/server.py` (headless load version guard), `src/backend/aircooling/runner/hybrid_air_heat_handoff.py` (read via `project_at(doc, 0)` when `schemaVersion == 5`), `src/backend/liquidcooling/liquidrun_schema.py` stays v4 for `source_project` until T3; Tests: extend their existing pytest files with a v5 fixture (migrated Shinaide).
- [x] Steps: failing test (v5 doc rejected) → accept 5 through `project_at` → pytest green → commit `transient(T1.8): server and hybrid handoff read cfdproj v5`.

### T1 gate
- node tests, Python tests, parity, vitest, `tsc -b`, `vite build` all green; t=0 regression over every `case/**/*.cfdproj`; browser smoke on 40901: open Shinaide + Base_coldAisleIso, inspector values unchanged, edit + save + reopen identical; receipt `docs/benchmarks/transient-keyframes-t1-<date>/`; merge to `main`.

---

## Stage T2 — UI, in three merges (expanded 2026-09-30 after T1)

Mapping of the React integration points (Explore, 2026-09-30) found that the T1 freeze only covered `store.edit`:
`notifyLiveEdit` (rotate gizmo, rack depth, opacity, every legacy liquid edit) and `mutateLive` drags bypass it, the
old ⏱ tab still writes `model.timeVarying` which migration deletes, and two inspector edits (ACU airflow, cold-plate
`Q_W`) touch an untracked field in the same edit and are refused off t = 0. T2a fixes that before any UI builds on it.
User rulings (2026-09-30): static = scene state only (confirmed); display-only fields are editable at any playhead.

### T2a — freeze correctness, mode switch, dead UI removal (vitest-gated + browser smoke)

> **T2a done (2026-09-30):** gate passed, receipt `docs/benchmarks/transient-keyframes-t2a-2026-09-30/`. Deviations: A3 is one guard in `mutateLive` (22 non-scene callers opt out); A4 needed no source change; the top-bar options use new keys 稳态 / 瞬态.

**Task A1: display-only allow-list.** `shared/transient/transient_params.json` gains
`"displayOnly": [{"key":"display","under":"meta"}, {"key":"moveTool"}, {"key":"rotateTool"},
{"key":"opacity","under":"faces.*"}, {"key":"wallOpacities","under":"faces.*"}]` (`under` = dotted suffix of the parent
path, `*` any key). `staticText` drops these keys wherever they occur in `geometry`/`constructions`.
Test (`test_cfdproj_transient.mjs`): off t = 0 an opacity / moveTool / face-opacity edit is accepted with no keyframe;
a face `name` edit is still `static`.

**Task A2: store freeze.** `projectStore.ts`:
- `sceneFrozen(): boolean` = `transient.mode === 'transient' && playhead > 0`.
- `edit()` returns `boolean` (false on refusal); `rejectionSeq()` increments on every refusal.
- Frozen reference: on entering a frozen state (setPlayhead, and after every accepted edit/undo/redo/load while frozen)
  keep `frozenRef = cloneProject(state.project)` and `frozenStatic = staticText(state.project)`; cleared at t = 0
  (t = 0 pays nothing).
- `notifyLiveEdit`: when frozen and `staticText(live) !== frozenStatic`, restore `state.project =
  materializeAt(cloneProject(frozenRef), playhead)`, set `lastRejection = {ok:false, reason:'static'}`, bump
  `rejectionSeq`, log `editRejected`, emit; otherwise the current tracked-field sync.
Tests (`projectStoreTransient.test.ts`): a legacy in-place position change + notify at t = 120 is reverted and
rejected; an in-place opacity change + notify is kept; an in-place power change + notify becomes a keyframe at 120;
`edit` returns false on refusal; at t = 0 nothing is frozen.

**Task A3 (done differently, 2026-09-30): one guard in `mutateLive` instead of per-entry blocks.** While frozen a scene `mutateLive` is a no-op (logged once a second); the 22 non-scene callers (opacity, tool toggles, case dir, export name, mesh seeds/zones, probes) pass `{ scene: false }`. This covers every drag entry, including ones not listed below; the gizmos still render and move their handles, hiding them is T2c. Original wording kept for the record: **Task A3: drag starts respect the freeze.** One predicate, `store.sceneFrozen()`, checked where a scene drag begins:
`interaction/moveGizmo.ts` onDragStart, `interaction/rotateModelGizmo.ts`, `geometry/faceDrag.ts` (the four drag
entries), `geometry/airPipeHandles.ts`, `room2d/room2dActions.ts`, `geometry/seedActions.ts`,
`geometry/rackDetail.ts` depth drag. A refused start logs `editRejected` once. `caseDir.ts` keeps its `mutateLive`
(meta is not scene state). Tests: one per entry that a frozen store makes the start a no-op (existing test files for
each module where they exist; otherwise `test/sceneFreezeDrags.test.ts`).

**Task A4 (no source change needed, 2026-09-30):** the commit blur also ends `editing`, whose re-sync restores the stored value; `test/components/NumberFieldRefusal.test.tsx` pins it. Original wording: **Task A4: refused numbers revert.** `components/NumberField.tsx` records `store.rejectionSeq()` before `onCommit` and
reverts the text when it changed (or when `onCommit` returns false). Test in `test/components/NumberField.test.ts`.

**Task A5: mixed edits.** `liquid/liquidColdPlates.ts` `updatePair`: set `preset='custom'` only when a non-tracked
numeric field changes. `geometry/patchPhysics.ts`: mirror the return patches' `volumeFlowRate` only when
`!store.sceneFrozen()`. Tests: off t = 0 a cold-plate `Q_W` edit and an ACU airflow edit each become one keyframe.

**Task A6: steady/transient switch in the top bar.** `components/Toolbar.tsx` gets `<div id="transient-mode-slot"/>`
beside `#renderer-switch-slot`; a `transient/ModeSwitch.tsx` select (稳态 / 瞬态, i18n keys) portals into it and calls
`setSimulationMode(mode)` — the ONE writer: an `edit` that sets `transient.mode` and mirrors
`runDefaults.simulationConfig.solverType`, and on switching to transient sets `simulationConfig.endTimeSeconds =
transient.durationS`. `SolverPanel`'s solverType radios call the same function. Readers of `solverType`
(`SolverPanel`, `AcuCoolingModel`, automation) keep working until T4. Tests: `test/modeSwitch.test.tsx`; update
`RendererControl.test.ts` toolbar-source assertions if needed.

**Task A7: automation.** `automation/contract.ts` exposes `setPlayhead`, `playheadTime`, `sceneFrozen`,
`setSimulationMode`, `lastEditRejection`. Test in `test/automationContract.test.ts`.

**Task A8: remove the dead transient UI.** Delete `components/TransientPanel.tsx`, `components/TimeVaryingEditor.tsx`,
`run/transient.ts`, `run/timeVaryingEdit.ts`, `run/transientTab.ts`, the `transient` left tab (`ui/leftTabs.ts`,
`App.tsx:729`), `store.setTimeVarying`; move `TransientRunBlock`'s run button into `SolverPanel` if still needed by
`solveLauncher.test.tsx`; delete their tests; update `leftTabs`, `anchorMarkers`, `supplyFlowVisibility`,
`viewportTabGate` tests and `parity/alignment.py`/`parity/id_diff.py`. i18n keys stay (shared with the legacy page).

**T2a gate:** vitest, `tsc -b`, `vite build`, node transient tests green; browser smoke on 40901 via automation
(`setPlayhead`): rotate/move/opacity/power at t = 120 behave as specified; mode switch round-trips through save/reopen;
receipt `docs/benchmarks/transient-keyframes-t2a-<date>/`; merge.

### T2b — TimelineStrip + TrackPanel (expanded 2026-09-30)

> **T2b done (2026-09-30):** gate passed, receipt `docs/benchmarks/transient-keyframes-t2b-2026-09-30/`. Found at the gate: the toolbar fit had to re-measure when the strip mounts, and the strip wraps in a narrow toolbar.

Placement (checked `PanelResizers.tsx`): the strip is the last row of `#toolbar` (`flex-basis:100%`, transient mode
only) — it joins the toolbar's natural-height measurement, is scaled with the toolbar when squeezed, hides when the
toolbar collapses, and is inert during a solve (fine for T2; revisit for result playback in T5). The TrackPanel is a
separate floating overlay anchored at the toolbar's bottom edge between the side panels (tracked like
`RendererControl` does), toggled from the strip, hidden when the toolbar is collapsed or the project is steady.

**Task B1: timeline operations** — `react/src/transient/trackEdits.ts`, pure functions over a `TransientBlock` draft:
`keyframeTimes`, `shiftTime(from,to)`, `moveKeyframe(key,i,t)`, `setKeyframe(key,i,{value?,rampS?})`,
`addKeyframe(key,t,value)`, `deleteKeyframe(key,i)` (t = 0 never), `setDuration(d,{trim})`, `setStart(start)`; times
snap to `gridS`. Each op runs on a clone first and is refused (with the validator's first message) unless
`validateTransient` passes; `applyTimelineOp(label, op)` then commits it as ONE `store.edit`. The duration writer also
mirrors `simulationConfig.endTimeSeconds` (same one-writer rule as the mode). Tests: `test/trackEdits.test.ts`.

**Task B2: TimelineStrip** — `react/src/transient/TimelineStrip.tsx` inside `Toolbar.tsx`: start control
(steadyConverged / uniform + air/water/solid temperatures), bar with union diamonds and a draggable playhead
(`setPlayhead`), click-to-seek, drag a diamond = `shiftTime`, double-click = exact time input, current time readout,
duration input (trim confirm when keyframes would fall off), TrackPanel toggle. i18n keys `transient.*`.
Tests: `test/timelineStrip.test.tsx` (render, seek, diamond drag with a mocked bar rect, duration trim confirm).

**Task B3: TrackPanel** — `react/src/transient/TrackPanel.tsx`: rows for the selected model's tracks
(`useAppSelection`), filtered to the current air/liquid mode; per row a lane with keyframes, value polyline and ramp
handles; drag keyframe (`moveKeyframe`), drag ramp handle (`setKeyframe` rampS), click keyframe → exact
time/value/ramp editor + delete, "+" adds a keyframe at the playhead with the value there. It is the editor for params
with no inspector field (cp `dT_K`, pump rpm, valve opening, CDU return pressure / primary inlet / primary flow),
which take physical effect only in T3. Param labels `transient.param.<key>`. Tests: `test/trackPanel.test.tsx`.

**T2b gate:** vitest / tsc / vite build / node; browser on 40901: seek, keyframe by inspector at t, diamond drag,
ramp handle, exact input, selection linkage, undo/redo, save/reopen; receipt; merge.

### T2.5 — liquid keyframes reach the v2/v3 compilers; air keyframes refused (added 2026-09-30, before T2c) — DONE (receipt `docs/benchmarks/transient-keyframes-t25-2026-09-30/`)
Found while planning T2c: T1's migration turns `loadStep`/`supplySchedule` into `cp.heat`/`cdu.primaryInletT`
keyframes and clears the settings, but `compileSavedExperiment` still reads only the settings — the three repo cases
with a load step (NV_PEK02_Mock, Shinaide_dae_cpu, Shinaide_hybrid_cpu) compile with the step silently gone. Air is
NOT a regression (the runner's registry is built from `boundaryBindings` only; the old UI's `model.timeVarying` never
reached them — design §4), but T2b now invites air keyframes, so an air transient export must say it cannot honour them.
1. `liquid/liquidSchedules.ts`: when the project is v5, `transient.mode === 'transient'` and the liquid run is
   time-marching (v2, or DAE `analysis.mode === 'transient'`), sample every track at `t_i = i·dt` (right-continuous,
   design §3), compile the graph once per run of identical samples, and emit: DAE v3 — `Q_W`/`rpm_ratio`/`opening`
   edge series and `inlet_K`/`flow_kg_s` primary-circuit series (scalar when constant); v2 — events for
   `Q_W`/`rpm_ratio`/`opening`. `N_t` from `transient.durationS`. Refuse: a varying liquid track with no v2/v3 home
   (`cp.dT`, `pump.flow`, `cdu.secondarySupplyT`, `cdu.returnPressure`; `cdu.primary*` on v2), a keyframe or ramp start
   off the dt grid, a legacy load step / supply schedule together with keyframes of the same input, any other graph
   change between samples.
2. Migration (JS + Python twin): a converted load step / supply schedule makes the block `transient` also for a v2
   (non-DAE) liquid experiment — its run was time-marching and consumed them.
3. `buildManifestV2`: refuse when the air solver is transient and an air track has a keyframe after t = 0 (T4 lands it).
4. Tests: the three cases compile with the step at the right sample; each refusal; steady mode compiles the start values.
5. Gate: vitest/pytest; browser — Shinaide DAE transient run shows the step; receipt; merge.

### T2c — inspector semantics + solve-tab trim (expanded 2026-09-30 after T2.5) — DONE (receipt `docs/benchmarks/transient-keyframes-t2c-2026-09-30/`)
Map (Explore, 09-30): 8 of the 14 registry params have an inspector field, all `NumberField` — server.power
`EquipmentInspector #eq-power`, box.heat `BoxCfdSection #box-total-power`, acu.supplyTemp/airflow/supplyAngle
`PatchInspector #acu-patch-temp/-flow/-angle`, cp.heat `ColdPlateSection #cpcu-{i}-Q_W`, pump.flow `CduInspector #cdu-flow`,
cdu.secondarySupplyT `CduInspector #cdu-supply-temp`; the other 6 are TrackPanel-only. No inspector context exists;
the inspector slot renders at `App.tsx` (geometry tab and the mesh tab's inspector sub-tab).
1. `transient/keyframeState.ts` `keyframeStateAt(keyframes, t)` → `'key' | 'ramp' | 'hold'`; `transient/KeyframeBadge.tsx`
   `<KeyframeBadge modelUid param target/>` (nothing in steady mode or without a track; ◆ key, ◐ ramp, ◇ hold, with a
   tooltip). Placed on the 8 fields; each such row gets class `kf-field`.
2. `transient/refusalText.ts` `refusalText(reason)`: the fixed English reasons of `trackEdits` ops and `validateTransient`
   (exact or pattern match, e.g. `value outside [a, b] unit`, `time not on the g s grid`) and the store's
   `'static' | 'structure'` codes → i18n; unknown text falls through unchanged. Used by the strip and the banner.
3. `transient/FrozenBanner.tsx` above the inspector slot (both places): while `sceneFrozen()` a one-line banner
   (t, "geometry and static fields are read-only; ◆/◇ fields write a keyframe here") and, after a refused edit
   (`rejectionSeq`), the localised reason for a few seconds. The slot wrapper gets `inspector-frozen`; CSS dims and
   de-points every input/select/button outside `.kf-field` / `.frozen-ok` (display-only controls: transform tool
   toggles, opacity sliders). Edits stay refused in the store regardless (T2a).
4. Viewport: move/rotate gizmos and the face-drag / air-pipe / junction / seed handles are not shown while frozen.
5. 求解 tab: SolverPanel drops the mode radios (top-bar select is the one writer) and `slv-end-time-s` (the timeline
   end is the duration; shown read-only in transient mode). LiquidExperimentPanel, **in transient mode only**: duration
   read-only from the timeline, load step and supply schedule hidden behind a hint to keyframe `cp.heat` /
   `cdu.primaryInletT`. Steady mode keeps them unchanged — the liquid-only v2/v3 scenario path the T2.5 compile still
   honours — until the open air/liquid mode ruling (design §6). Parity map: solverType → `transient-mode-selector`,
   endTimeSeconds → `timeline-duration`.
6. Gate: vitest; browser (Base_coldAisleIso + NV_PEK02): badge states at t = 0 / on a keyframe / mid-ramp / hold,
   inspector edit at t > 0 on a tracked field writes a keyframe, a static edit is refused with a Chinese reason,
   gizmos hidden at t > 0 and back at 0, solve tab trimmed; receipt; merge.

## Stage T3 — liquid (.liquidrun v4) (expanded 2026-09-30 after T2c) — DONE (receipt `docs/benchmarks/transient-keyframes-t3-2026-09-30/`)
Map (Explore, 09-30): the v3 DAE integrates backward Euler only (`dae_newton.py`); inputs are sampled per step in
`EquationGraph._parameter_fields` (`solver/equation_graph.py`); v3 validation reuses the strict v2 models through
`_v2_graph_view` (`liquidrun_dae_schema.py`), so every new field must be stripped/filled there and attached to the
compiled graph afterwards (as `bc_T_series` is). Residual rows: pump momentum (`pump_headloss`), HX secondary /
primary energy (`exchange = UA·(T_s − T_p)`), node pressure scatter (`_pressure`), cold-plate solid (`heat_W`).

### T3a — backend: v4 schema, loader, the four forms (pytest on 40901)
1. `CompiledGraph` gains optional `bc_P_series [N_nodes,N_t]`, `pump_flow_mode [N_pump] bool` + `pump_flow_set [N_pump,N_t]`,
   `cp_rise_mode [N_cp] bool` + `cp_dT_set [N_cp,N_t]`, `hx_setpoint_mode [N_hx] bool` + `hx_T_set [N_hx,N_t]`
   (None = v3 behaviour); `time_slice` selects the series.
2. `EquationGraph`: new θ fields appended after the existing ones (`pressure_bc` sampled from `bc_P_series`;
   `pump_flow_set`, `cp_dT_set`, `hx_T_set`, `hx_T_set_rate` = backward difference on the dt grid, 0 at i = 0);
   residual branches per element, modes fixed for the run:
   - (a) HX setpoint: secondary row `G·(T_s − T_set)` (G = 4000 W/K, the gauge scaling); secondary leaves the
     differential mask; implied duty `Q = ṁ·cp·(T_up − T_s) − C_s·Ṫ_set` enters the primary row; diagnostics
     storage uses `C_s·Ṫ_set`; initial guess `T_s = T_set(0)`.
   - (b) pump flow: momentum row `ṁ − ṁ_set(t)` with residual scale 1 (like a closed valve); pressure rise
     follows from the network.
   - (c) pressure BC series: `pressure_bc` sampled per step (`bc_P` stays the 1-D device/dtype reference).
   - (d) cold plate temperature rise: `heat_W = |ṁ|·cp·ΔT_set(t)` (no flow → no heat), used by the solid row and
     by diagnostics' heat input.
   `parameters()` takes overrides for the new fields; the heat multiplier also scales `ΔT_set`.
3. `LiquidrunDAEV4` (subclass of V3: `schemaVersion 4`, `version '4.0'`, `events` must be empty, required
   `transient` provenance {start, durationS, tracks[]}); edge params gain HX `mode/T_set_K`, pump `mode/flow_set_kg_s`,
   cold plate `heat_mode/dT_set_K`, node `bc.P_Pa` scalar | list[N_t]; cross-checks (series length N_t; mode ⇔
   field; no `Q_W` on a temperature-rise plate; flow-mode pump without `rpm_ratio` gets an internal placeholder).
   `_v2_graph_view` strips/fills v4 fields (no-op on v3). `load_dae_input` dispatches on schemaVersion and
   attaches the tensors. `liquidrun_dae_v4.schema.json` generated beside the v3 file (v3 file unchanged).
4. Tests (`test_liquid/test_dae_v4_forms.py`): per form an exact / manufactured check on the `small_graph`
   fixture — (a) and (d) time-refinement MMS at backward-Euler order reusing the coupled manufactured trajectory;
   (b) flow = set point and pressure rise = network loss; (c) pressures shift by ΔP with flows unchanged —
   each with the energy diagnostics closed; a v4 manifest through `load_dae_input` + `DAEExperiment` with a step in
   each input; all v3 liquid tests stay green.
5. Runtime accepts v4: `run_manager` (dispatch, `input.liquidrun` write), `runner.py`, `dae_runtime`,
   `accepted_run_input` (heat multiplier), `hybrid_heat_audit` (temperature-rise plates: heat from frames' edge flow ×
   cp × ΔT_set), `hybrid_air_heat_handoff`; tests.

### T3b — frontend: modes in the project, v4 compile, gate
1. Static mode fields: CDU `cooling.secondaryControl: 'physics'|'setpoint'`, `cooling.pumpControl: 'curve'|'flow'`;
   cold-plate pair `heatMode: 'power'|'temperature_rise'`; selects in `CduInspector` / `ColdPlateSection`.
2. `dynamicLiquidGraph` reads the registry fields it ignores today: pump `rpmRatio`, return node
   `returnPressureKpa`, and — only in the non-default modes — HX `T_set_K` from `supplyTemperatureC`, pump
   `flow_set_kg_s` from `totalFlowRate` (L/min → kg/s), plate `dT_set_K` from `dT_K` (default modes emit nothing, so
   v3/v2 outputs stay identical).
3. `makeLiquidrunDAEV4` + sampler: the T2.5 per-run graph compile emits `list[N_t]` for every varying edge / node
   field (no events), plus primary-circuit series; `transient` provenance and `analysis.initialization` from the
   timeline start (steadyConverged → steady; uniform → consistent with its temperatures). Refuse a varying track whose
   mode is off (e.g. secondary supply keyframes with a physics HX). The DAE path compiles v4; v2 refuses non-default
   modes.
4. Gate: Shinaide DAE transient in the browser — secondary setpoint step, pump-flow ramp, return-pressure change —
   runs; results show the HX outlet on the set point, pump flow on its ramp, pressures shifted; receipt; merge.

## Stage T4 — air (cfdrun v3) (expanded 2026-09-30 after T3)
Map (Explore, 09-30): no keyframe reaches OpenFOAM today (v5 migration deletes `model.timeVarying`, the exporter reads only
that); the runner already turns `parameters.timeVarying` into OpenFOAM `table`s (`fields.py` `_format_time_table`,
ACU airflow/supply temperature, heat-source q) and fan-outlet ΔT into a `time()` ternary expression; wall-mounted ACU U
ignores it; split server faces drop it; no steady → transient chain exists. The runner reads the meshGeometry blob's
simulationConfig (parity-checked against the root one). "cfdrun V3" is also the name of the proposed thin-contract
migration (`docs/superpowers/specs/2026-07-08-cfdrun-v3-thin-contract-design.md`, only its Stage 1 shadow exists, no
code checks `schemaVersion == 3`): E7 takes schemaVersion 3 as the user named it; that migration ships under the next
number. Removing the blob's duplicate simulationConfig is that migration's job, not T4's — for v3 the transient timing
stays mirrored (endTimeSeconds = durationS) and parity-checked.

**T4a — probe (done 09-30, 40901, OpenFOAM v2412, `tutorials/.../buoyantBoussinesqPimpleFoam/hotRoom`):** two rows at the
same time → `FOAM FATAL ERROR: out-of-order value` (TableBase::initialise); the ε form `(t−1e-6 v_old)(t v_new)` runs and
writes v_new at t (right-continuous, design §1.2); `interpolationScheme linear; outOfBounds clamp;` gives 300/310/320 at
2/3/4 s on a (2 s, 300)→(4 s, 320) ramp.

**T4b — exporter + schema:** a transient air run (solverType transient) with air keyframes emits schemaVersion 3 =
v2 + `transient {durationS, start}` + per-binding `parameters.schedules {param: [[t, v], …]}` (corner rows; a step is
two rows at one t). Built like the liquid sampler: the manifest's bindings are evaluated just before and at every
corner time of the union of air tracks (geometry frozen, one STL map) and diffed — exact at corners, linear between
(design §4). Schedulable: acu_supply `temperatureC`, `flowRateM3s`, `directionWorld` (+ `supplyAngleDeg`); fan
`powerW` (+ derived outlet `temperatureC`); `heat_source_flux` `heatFluxWm2`. Any other binding change → refused;
`server.power` on a heat-flux server (it reads inlet/outlet power) → refused by name. Split server faces keep their
schedule (÷ n like Q and P). JS `validateManifest` and Python `CfdrunV3` accept 3; v2 unchanged.
**T4c — runner BCs:** v3 schedules → the existing internal timeVarying keys (airflow, supplyTemp, power, heatOutput);
`_format_time_table` writes same-t rows as `(t−ε v_old)(t v_new)` (ε = 1e-6 s) with `interpolationScheme linear;
outOfBounds clamp;`; wall-mounted supply U becomes a vector table `Q(t)/A·dir(t)`; fan-outlet ΔT(t) from a power
schedule becomes a piecewise-LINEAR `time()` expression (ramps, not only steps).
**T4d — start chain:** `start.kind == steadyConverged` → SIMPLE with the t = 0 values to convergence, reconstruct the
latest time, write its internal fields into the transient case's `0/` (same mesh, BCs regenerated with tables), then
PIMPLE for durationS; `uniform` → PIMPLE from U = 0 and T = airT_C.
**T4e — gate:** unit tests (exporter schedules, schema, BC text, ε, vector table, expression, internal-field seeding);
a short real run on a small case (Slab) with a supply-temperature step, an airflow ramp and a server-power step:
patch averages at written times match the tracks; browser gate; receipt; merge.

**Status (09-30):** T4a–T4e done — receipt `docs/benchmarks/transient-keyframes-t4-2026-09-30/`. The gate found and fixed
a pre-step sample inside the evaluator's EPS (steps at t ≤ 1 s) and a rotated-log naming slip; the ACU return patch
needs no schedule (pressure outlet, design §6 T4 item 6).

## Stage T5 — results on the timeline + E2E (expanded 2026-09-30 after T4)
Map (Explore, 09-30): no result code reads the playhead. Liquid: `LiquidResultsPanel` keeps a local `sample` and
writes the global `liquidTempColor.setResultSample` (Three.js + OV colour follow that store); `time_s` is in the
timeline's seconds. Air: `PostprocessPanel` picks a time with `#pp-timestep` from `/api/scan-case-status` `timesteps`;
a time change clears the frame, slices and streamlines and needs 启用后处理 again; `/api/pp/init` already takes
`timeValue` (one cached frame, full native read per time). Nothing marks a run transient. `convergence.json` has no
consumer but the runner's log line.

Rules (design §2 "同一播放头可擦洗结果"): results follow the playhead **when the user moves it** (or the result's own
time control), not on load — a fresh result still opens at its latest sample/time, as today. Nearest sample / written
time, ties to the earlier. A result's own time control moves the playhead too (guarded so the playhead's snap to
`gridS` never overrides the pick). Only for a transient project and a transient result. Wherever a result is shown,
a readout names the **result's** time (nearest ≠ equal, and air `0/` is not a result — its boundary `value`s are
generated placeholders). Colour ranges (surface clim, slice display range) are **held** across playhead-driven frame
changes and reset only on a run change, so frames stay comparable. The playback subscription is
`startResultPlayback(): () => void` (idempotent, returns the unsubscribe), called on App mount and explicitly in tests.
Known limits to record, not fix: with `gridS` 1 and writes every 0.5 s, half the air frames are reachable only from
`#pp-timestep`; a result-control pick moves the playhead off 0, which freezes the inspector (回到起点 recovers it).

**T5a — liquid:** `transient/resultPlayback.ts`: `nearestIndex(times, t)`; a module subscription (started once with
the app) maps playhead moves to `setResultSample(nearest)` when the project is transient and the loaded liquid result
is transient. `LiquidResultsPanel` reads the store's sample (subscribe) instead of local state; its slider sets the
sample and the playhead. Tests: nearest/ties; playhead → sample; slider → playhead without being overridden; a
steady project or steady result is untouched; new run resets to latest.
**T5b — air:** server `scan-case-status` adds `transient` per run (the run's `system/controlDict` `application` is a
PIMPLE solver). `PostprocessPanel`: on a transient run that has been initialised, a playhead move (debounced, previous
init aborted) selects the nearest written time and re-initialises at it; `#pp-timestep` picks move the playhead.
Slices and streamlines are re-requested at the new time from their stored parameters instead of being dropped.
Tests: server flag (PIMPLE / SIMPLE / steady-start chain), nearest time, re-init at the new time, slices kept.
**T5c — transient verdict:** `convergence.assess_run` on a transient run (PIMPLE `application`): `TRANSIENT_COMPLETED`
when the log reached `endTime` without a FOAM fatal and the latest fields are finite, else `TRANSIENT_INCOMPLETE`;
diagnostics without invented thresholds — time steps, final time, max Courant vs `maxCo`, per-field initial residual
of each step's last outer corrector (median / max over the last window). Steady runs unchanged. **Only the last solver
stage is assessed**: a steady-converged start writes SIMPLE (1…245) then PIMPLE (0.005…4) into one job log, and today's
verdict mixes them (T4 gate run: 1045 "iterations", Ux/Uy/Uz with 245 SIMPLE-only samples) — parse from the last
`Exec :` banner. Tests on synthetic PIMPLE logs and on both T4 gate runs' logs as fixtures (trimmed).
**T5d — gate:** first check that a T4 gate run copied into a scratch case passes `/api/pp/init`'s run identity (else
budget a fresh solve). Browser E2E on a gate worktree: air (Slab transient run, playhead scrubs the surface field and a slice,
values match the T4 patch averages), liquid (Shinaide DAE transient via the timeline, playhead scrubs the sample,
Three.js colour and panel readout follow); receipt; LEDGER/MAINLINES; merge.


**Status (09-30):** T5a–T5d done — receipt `docs/benchmarks/transient-keyframes-t5-2026-09-30/`; **E7 complete**.
The gate found and fixed a freshly opened air run jumping to the playhead's frame (anchor rule, `0a37b988`).
