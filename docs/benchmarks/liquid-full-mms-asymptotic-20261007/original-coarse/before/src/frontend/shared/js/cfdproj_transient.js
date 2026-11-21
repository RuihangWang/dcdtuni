/**
 * cfdproj v5 transient keyframes: the one definition of what a track is, how it
 * evaluates, and how it maps onto model fields.
 *
 * Design: docs/developer/transient_keyframes_design_2026-09-30.md (§1).
 *
 * - A registry (shared/transient/transient_params.json) names every keyframable
 *   field by a path template relative to its model. Only those fields can vary
 *   in time; everything else is static (geometry is frozen in a transient run).
 * - Tracks are the whole state of those fields: on disk the fields are absent
 *   from the models and live only in `transient.tracks`. In memory the editor
 *   keeps them materialised at the playhead (materializeAt) so every existing
 *   reader works unchanged; edits come back as keyframes (commitEdit).
 * - Keyframe (t, value, rampS): step by default; with rampS > 0 the value ramps
 *   linearly over [t - rampS, t]; right-continuous at t.
 *
 * UMD like the other .cfdproj modules: attaches to `window.cfdprojTransient`
 * in the browser and to `module.exports` under node. A Python twin
 * (scripts/cfdproj_transient.py) mirrors it; test_cfdproj_transient_parity.py
 * keeps the two in step.
 */
(function () {
  'use strict';

  const EPS = 1e-9;
  let REGISTRY = null;

  function registry() {
    if (REGISTRY) return REGISTRY;
    if (typeof window !== 'undefined' && window.__TRANSIENT_PARAMS__) REGISTRY = window.__TRANSIENT_PARAMS__;
    else if (typeof require === 'function') REGISTRY = require('../transient/transient_params.json');
    else throw new Error('cfdproj_transient: parameter registry not loaded');
    return REGISTRY;
  }
  /** Browser bundles inject the JSON (Vite) before first use. */
  function setRegistry(r) { REGISTRY = r; }
  function entryOf(key) { return registry().params.find((e) => e.key === key); }

  // --- path templates ---------------------------------------------------------
  // Segments split on '.': `{var}` binds an object key or array index;
  // `seg[id={var}]` selects the element of array `seg` whose `id` equals the
  // bound value; `*` iterates an object's keys without binding. `where` is
  // checked on the object chosen by the LAST binding selector.

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
  function fmt(s) {
    if (s.kind === 'key') return s.key;
    if (s.kind === 'var') return `{${s.name}}`;
    if (s.kind === 'any') return '*';
    return `${s.key}[id={${s.name}}]`;
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
      for (const el of arr) {
        if (el && el.id != null) walk(el, segs, i + 1, bindings.concat(String(el.id)), el, where, leaf);
      }
    }
  }

  /** Every concrete instance of `entry` inside `model` that carries a value
   * (or, for `createWithDefault` entries, would take the default). */
  function enumerateTargets(model, entry) {
    const out = [];
    const segs = parse(entry.path);
    walk(model, segs, 0, [], model, entry.where, (parent, key, bindings) => {
      const value = parent[key];
      if (value !== undefined && value !== null) out.push({ target: bindings.join('/'), value });
      else if (entry.createWithDefault) out.push({ target: bindings.join('/'), value: entry.default });
    });
    if (!out.length && entry.createWithDefault && segs.every((s) => s.kind === 'key')) {
      out.push({ target: '', value: entry.default });
    }
    return out;
  }

  /** Parent object and key of (entry, target); plain-key parents are created when `create`. */
  function locate(model, entry, target, create) {
    const segs = parse(entry.path);
    const vals = target === '' ? [] : String(target).split('/');
    let obj = model;
    let vi = 0;
    for (let i = 0; i < segs.length - 1; i++) {
      const s = segs[i];
      if (obj == null || typeof obj !== 'object') return null;
      if (s.kind === 'key') {
        if (obj[s.key] == null) { if (!create) return null; obj[s.key] = {}; }
        obj = obj[s.key];
      } else if (s.kind === 'var') {
        const k = vals[vi++];
        obj = Array.isArray(obj) ? obj[Number(k)] : obj[k];
      } else if (s.kind === 'idsel') {
        const id = vals[vi++];
        obj = (Array.isArray(obj[s.key]) ? obj[s.key] : []).find((el) => el && String(el.id) === id);
      } else {
        const rest = Object.assign({}, entry, { path: segs.slice(i + 1).map(fmt).join('.') });
        const restTarget = vals.slice(vi).join('/');
        for (const k of Object.keys(obj)) {
          const hit = locate(obj[k], rest, restTarget, false);
          if (hit) return hit;
        }
        return null;
      }
    }
    if (obj == null || typeof obj !== 'object') return null;
    return { parent: obj, key: segs[segs.length - 1].key };
  }
  function getAt(model, entry, target) {
    const h = locate(model, entry, target, false);
    return h ? h.parent[h.key] : undefined;
  }
  function setAt(model, entry, target, value) {
    const h = locate(model, entry, target, true);
    if (!h) throw new Error(`cfdproj_transient: no field for ${entry.key} target "${target}"`);
    h.parent[h.key] = value;
  }
  function deleteAt(model, entry, target) {
    const h = locate(model, entry, target, false);
    if (h) delete h.parent[h.key];
  }

  // --- evaluation --------------------------------------------------------------

  /** Value of a keyframe list at time t (step; ramp over [t_k − rampS, t_k]; right-continuous). */
  function valueAt(kfs, t) {
    let prev = kfs[0].value;
    for (let k = 1; k < kfs.length; k++) {
      const tk = kfs[k].t;
      const vk = kfs[k].value;
      const r = kfs[k].rampS || 0;
      if (t >= tk - EPS) { prev = vk; continue; }
      if (r > 0 && t >= tk - r) return prev + (vk - prev) * (t - (tk - r)) / r;
      return prev;
    }
    return prev;
  }

  /** Piecewise-linear corner rows [[t, v], …]; a step is two rows at the same t.
   * Compilers (OpenFOAM tables, DAE sampling) consume this form. */
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

  function onGrid(x, g) {
    const q = x / g;
    return Math.abs(q - Math.round(q)) < 1e-9 * Math.max(1, Math.abs(q));
  }

  /** Errors of a transient block as [{path, msg}] (empty = valid). */
  function validateTransient(tr) {
    const errs = [];
    const push = (path, msg) => errs.push({ path, msg });
    if (!tr || typeof tr !== 'object') return [{ path: 'transient', msg: 'missing transient block' }];
    if (tr.mode !== 'steady' && tr.mode !== 'transient') push('transient.mode', 'mode must be steady or transient');
    for (const key of ['airMode', 'liquidMode']) {
      if (tr[key] !== undefined && tr[key] !== 'steady' && tr[key] !== 'transient') push(`transient.${key}`, `${key} must be steady or transient`);
    }
    if ((tr.airMode === 'steady' || tr.airMode === 'transient') && (tr.liquidMode === 'steady' || tr.liquidMode === 'transient') &&
        tr.mode !== modeOfDomains(tr.airMode, tr.liquidMode)) {
      push('transient.mode', 'mode must be transient exactly when airMode or liquidMode is');
    }
    if (!(tr.durationS > 0)) push('transient.durationS', 'duration must be > 0');
    if (!(tr.gridS > 0)) push('transient.gridS', 'grid must be > 0');
    if (!tr.start || !['steadyConverged', 'uniform'].includes(tr.start.kind)) {
      push('transient.start', 'start must be steadyConverged or uniform');
    }
    const seen = new Set();
    (Array.isArray(tr.tracks) ? tr.tracks : []).forEach((trk, i) => {
      const p = `transient.tracks[${i}]`;
      const entry = entryOf(trk && trk.param);
      if (!entry) { push(p, `unknown parameter ${trk && trk.param}`); return; }
      const id = `${trk.modelUid}|${trk.param}|${trk.target}`;
      if (seen.has(id)) push(p, 'duplicate track');
      seen.add(id);
      const k = Array.isArray(trk.keyframes) ? trk.keyframes : [];
      if (!k.length || k[0].t !== 0 || (k[0].rampS || 0) !== 0) push(p, 'a track needs a keyframe at t = 0 without ramp');
      k.forEach((f, j) => {
        const q = `${p}.keyframes[${j}]`;
        if (!Number.isFinite(f.value) || f.value < entry.min || f.value > entry.max) {
          push(q, `value outside [${entry.min}, ${entry.max}] ${entry.unit}`);
        }
        if (j === 0) return;
        const r = f.rampS || 0;
        if (!(f.t > k[j - 1].t)) push(q, 'keyframe times must be strictly increasing');
        if (r < 0 || r > f.t - k[j - 1].t + EPS) push(q, 'ramp longer than the gap to the previous keyframe');
        if (tr.gridS > 0 && (!onGrid(f.t, tr.gridS) || !onGrid(f.t - r, tr.gridS))) {
          push(q, `time not on the ${tr.gridS} s grid`);
        }
        if (f.t > tr.durationS + EPS) push(q, 'keyframe after the end of the timeline');
      });
    });
    return errs;
  }

  // --- per-domain modes (E8b) -----------------------------------------------
  // Air and liquid each run steady or transient; `mode` is derived (transient
  // when either is) and kept for readers of the timeline. airMode is mirrored
  // to simulationConfig.solverType (the air runner's truth), liquidMode to the
  // DAE's analysis.mode.
  function modeOfDomains(air, liquid) { return air === 'transient' || liquid === 'transient' ? 'transient' : 'steady'; }
  function liquidExperimentOf(doc) {
    const exp = doc && doc.runDefaults && doc.runDefaults.liquid && doc.runDefaults.liquid.experiment;
    return exp && typeof exp === 'object' ? exp : null;
  }
  /** {air, liquid} of a document. Blocks written before E8b derive them from
   * what each run actually followed: air from solverType; liquid followed the
   * timeline when it was transient, unless its DAE was steady — so a migrated
   * hybrid is air steady, liquid transient, and a steady timeline stays steady. */
  function domainModes(doc) {
    const tr = (doc && doc.transient) || {};
    const ok = (m) => m === 'steady' || m === 'transient';
    const sim = (doc && doc.runDefaults && doc.runDefaults.simulationConfig) || {};
    const dae = (liquidExperimentOf(doc) || {}).dae;
    const air = ok(tr.airMode) ? tr.airMode : (sim.solverType === 'transient' ? 'transient' : 'steady');
    const daeSteady = !!(dae && dae.analysis && dae.analysis.mode === 'steady');
    const liquid = ok(tr.liquidMode) ? tr.liquidMode : (tr.mode === 'transient' && !daeSteady ? 'transient' : 'steady');
    return { air, liquid };
  }
  /** The block of `doc` with both domain modes present and `mode` derived (in place). */
  function withDomainModes(doc) {
    if (!doc || !doc.transient) return doc;
    const m = domainModes(doc);
    doc.transient.airMode = m.air;
    doc.transient.liquidMode = m.liquid;
    doc.transient.mode = modeOfDomains(m.air, m.liquid);
    return doc;
  }

  /** A steady, track-less block (new documents; v5 files that lack one). */
  function defaultTransient(doc) {
    const roomT = Number((doc && doc.geometry && doc.geometry.temperature) ?? 22);
    return {
      mode: 'steady', airMode: 'steady', liquidMode: 'steady', durationS: 600, gridS: 1,
      start: { kind: 'uniform', uniform: { airT_C: Number.isFinite(roomT) ? roomT : 22, waterT_C: 30, solidT_C: 40 } },
      tracks: [],
    };
  }

  // --- tracks ↔ fields --------------------------------------------------------

  function forEachModel(doc, fn) {
    const buckets = (doc && doc.constructions) || {};
    for (const [bucket, items] of Object.entries(buckets)) {
      if (!items || typeof items !== 'object') continue;
      for (const [uid, model] of Object.entries(items)) {
        if (model && typeof model === 'object') fn(bucket, uid, model);
      }
    }
  }
  function entriesFor(bucket) { return registry().params.filter((e) => e.bucket === bucket); }
  function trackKey(uid, param, target) { return `${uid}|${param}|${target}`; }

  /** Map trackKey → {uid, entry, target, value} of every registry instance present in `doc`. */
  function snapshotTargets(doc) {
    const out = new Map();
    forEachModel(doc, (bucket, uid, model) => {
      for (const e of entriesFor(bucket)) {
        for (const { target, value } of enumerateTargets(model, e)) {
          out.set(trackKey(uid, e.key, target), { uid, entry: e, target, value });
        }
      }
    });
    return out;
  }

  /** A t = 0 track for every registry instance whose field is present (or defaulted). */
  function tracksFromFields(doc) {
    return [...snapshotTargets(doc).values()].map(({ uid, entry, target, value }) =>
      ({ modelUid: uid, param: entry.key, target, keyframes: [{ t: 0, value: Number(value) }] }));
  }
  function modelIndex(doc) {
    const m = new Map();
    forEachModel(doc, (bucket, uid, model) => m.set(uid, model));
    return m;
  }
  function tracksOf(doc) { return (doc && doc.transient && Array.isArray(doc.transient.tracks)) ? doc.transient.tracks : []; }

  /** In place: every tracked field := its track's value at t. */
  function materializeAt(doc, t) {
    const idx = modelIndex(doc);
    for (const trk of tracksOf(doc)) {
      const model = idx.get(trk.modelUid);
      const e = entryOf(trk.param);
      if (model && e && trk.keyframes && trk.keyframes.length) setAt(model, e, trk.target, valueAt(trk.keyframes, t));
    }
    return doc;
  }
  /** In place: delete every tracked field (what is written to disk). */
  function stripTrackedFields(doc) {
    const idx = modelIndex(doc);
    for (const trk of tracksOf(doc)) {
      const model = idx.get(trk.modelUid);
      const e = entryOf(trk.param);
      if (model && e) deleteAt(model, e, trk.target);
    }
    return doc;
  }
  /** Canonical text of everything that is NOT time-variable: the doc without
   * `transient` and without its tracked fields. */
  // A display-only rule matches a key whose parent path ends with `under`
  // (dotted, `*` = any key); without `under` it matches at any depth.
  function isDisplayOnly(key, parents) {
    return (registry().displayOnly || []).some((r) => {
      if (r.key !== key) return false;
      if (!r.under) return true;
      const want = r.under.split('.');
      if (want.length > parents.length) return false;
      const tail = parents.slice(parents.length - want.length);
      return want.every((w, i) => w === '*' || w === tail[i]);
    });
  }
  // Returns whether the node was emptied by the drop: an object that held
  // only display-only fields (a `meta` created to carry `display`) is itself
  // display-only and goes too.
  function dropDisplayOnly(node, parents) {
    if (Array.isArray(node)) { node.forEach((x, i) => dropDisplayOnly(x, parents.concat(String(i)))); return false; }
    if (!node || typeof node !== 'object') return false;
    let dropped = false;
    for (const k of Object.keys(node)) {
      if (isDisplayOnly(k, parents) || dropDisplayOnly(node[k], parents.concat(k))) { delete node[k]; dropped = true; }
    }
    return dropped && Object.keys(node).length === 0;
  }

  // Scene state only: the room and every model, minus the fields tracks own
  // and the display-only fields (opacity, tool toggles). Project settings
  // (meta, runDefaults, instrumentation, models) carry no time and stay
  // editable at any playhead; `transient` is the timeline itself.
  function staticText(doc) {
    const copy = JSON.parse(JSON.stringify({ geometry: doc.geometry, constructions: doc.constructions }));
    stripTrackedFields({ constructions: copy.constructions, transient: { tracks: tracksOf(doc) } });
    dropDisplayOnly(copy, []);
    return JSON.stringify(copy);
  }
  function upsertKeyframe(trk, t, value) {
    const k = trk.keyframes;
    const i = k.findIndex((f) => Math.abs(f.t - t) < EPS);
    if (i === 0) { k[0] = { t: 0, value }; return; }
    if (i > 0) { k[i] = Object.assign({}, k[i], { value }); return; }
    let at = k.findIndex((f) => f.t > t);
    if (at < 0) at = k.length;
    k.splice(at, 0, { t, value, rampS: 0 });
    const next = k[at + 1];
    if (next && (next.rampS || 0) > next.t - t) next.rampS = next.t - t;
  }

  /**
   * Turn an edit of the materialised project into track changes.
   *
   * `before` is materialised at t; `draft` is the edited copy (mutated here:
   * its `transient.tracks`). Field changes become keyframes at t. At t = 0 the
   * structure may change (servers, patches, valves appearing or vanishing) and
   * tracks follow it; elsewhere geometry is frozen, so a structural or static
   * change refuses the whole edit. Edits that only touch `transient` (timeline
   * work) are always accepted; the caller re-materialises afterwards.
   */
  function commitEdit(before, draft, t) {
    const tr = draft.transient || (draft.transient = defaultTransient(draft));
    if (!Array.isArray(tr.tracks)) tr.tracks = [];
    const a = snapshotTargets(before);
    const b = snapshotTargets(draft);
    const added = [...b.keys()].filter((k) => !a.has(k));
    const removed = [...a.keys()].filter((k) => !b.has(k));
    const atStart = Math.abs(t) < EPS;
    if (!atStart && (added.length || removed.length)) {
      return { ok: false, reason: 'structure', detail: added.concat(removed).slice(0, 5) };
    }
    if (!atStart && staticText(before) !== staticText(draft)) return { ok: false, reason: 'static', detail: [] };
    const find = (key) => tr.tracks.find((x) => trackKey(x.modelUid, x.param, x.target) === key);
    for (const key of removed) {
      const trk = find(key);
      if (trk) tr.tracks.splice(tr.tracks.indexOf(trk), 1);
    }
    for (const key of added) {
      const s = b.get(key);
      if (!find(key)) tr.tracks.push({ modelUid: s.uid, param: s.entry.key, target: s.target, keyframes: [{ t: 0, value: Number(s.value) }] });
    }
    for (const [key, s] of b) {
      const old = a.get(key);
      if (!old || Number(old.value) === Number(s.value)) continue;
      const trk = find(key);
      if (trk) upsertKeyframe(trk, atStart ? 0 : t, Number(s.value));
    }
    return { ok: true };
  }

  /** In place: fold fields changed OUTSIDE commitEdit (legacy in-place edits
   * followed by a notify) back into the tracks at t. At t = 0 new instances
   * get tracks and vanished ones lose theirs. */
  function syncFieldsToTracks(doc, t) {
    const tr = doc.transient || (doc.transient = defaultTransient(doc));
    if (!Array.isArray(tr.tracks)) tr.tracks = [];
    const cur = snapshotTargets(doc);
    const byKey = new Map(tr.tracks.map((x) => [trackKey(x.modelUid, x.param, x.target), x]));
    const atStart = Math.abs(t) < EPS;
    for (const [key, s] of cur) {
      const trk = byKey.get(key);
      if (!trk) {
        if (atStart) tr.tracks.push({ modelUid: s.uid, param: s.entry.key, target: s.target, keyframes: [{ t: 0, value: Number(s.value) }] });
        continue;
      }
      if (Number(s.value) !== valueAt(trk.keyframes, t)) upsertKeyframe(trk, atStart ? 0 : t, Number(s.value));
    }
    if (atStart) tr.tracks = tr.tracks.filter((x) => cur.has(trackKey(x.modelUid, x.param, x.target)));
    return doc;
  }

  // --- migration v4 → v5 --------------------------------------------------------

  function migrateToV4(raw) {
    if (typeof window !== 'undefined' && typeof window.migrateCfdprojToV4 === 'function') return window.migrateCfdprojToV4(raw);
    if (typeof require === 'function') return require('./cfdproj_migrate.js').migrateCfdprojToV4(raw);
    throw new Error('cfdproj_transient: v4 migrator not loaded');
  }
  const round9 = (x) => Math.round(x * 1e9) / 1e9;
  const kToC = (k) => round9(k - 273.15);

  /** Old `timeVarying` points interpolate LINEARLY between consecutive points;
   * the editor faked a step with a second point 0.1 s later. Lossless mapping:
   * a hold point adds nothing, a ≤0.1 s pair is a step at the earlier time,
   * any other change is a keyframe whose ramp spans the gap. */
  function keyframesFromPoints(points) {
    const kfs = [{ t: 0, value: points[0].value }];
    for (let i = 1; i < points.length; i++) {
      const p = points[i];
      const q = points[i - 1];
      if (p.value === q.value) continue;
      const gap = round9(p.t - q.t);
      const kf = gap <= 0.1 + 1e-9 ? { t: q.t, value: p.value, rampS: 0 } : { t: p.t, value: p.value, rampS: gap };
      const last = kfs[kfs.length - 1];
      if (kf.t <= last.t + EPS) {
        if (last.t === 0) kfs[0] = { t: 0, value: kf.value };
        else last.value = kf.value;
      } else kfs.push(kf);
    }
    return kfs;
  }

  /** The doc after v4→v5, plus human-readable notes on what was converted. */
  function migrateCfdprojV4toV5WithReport(raw) {
    if (!raw || typeof raw !== 'object') return { doc: raw, report: [] };
    if (Number(raw.schemaVersion || 0) >= 5) return { doc: raw, report: [] };
    const base = Number(raw.schemaVersion || 0) < 4 ? migrateToV4(raw) : raw;
    const doc = JSON.parse(JSON.stringify(base));
    const report = [];
    const tracks = tracksFromFields(doc);
    const find = (uid, param, target) => tracks.find((x) => x.modelUid === uid && x.param === param && x.target === target);
    const exp = doc.runDefaults && doc.runDefaults.liquid && doc.runDefaults.liquid.experiment;
    const dae = exp && typeof exp === 'object' ? exp.dae : null;
    const sim = (doc.runDefaults && doc.runDefaults.simulationConfig) || {};
    const liquidTransient = !!(dae && dae.analysis && dae.analysis.mode === 'transient');
    let mode = sim.solverType === 'transient' || liquidTransient ? 'transient' : 'steady';

    // Primary circuits: their scalar inlet/flow are the CDU's starting values.
    for (const pc of (dae && Array.isArray(dae.primary_circuits) ? dae.primary_circuits : [])) {
      const uid = String((pc && pc.edge_id) || '').replace(/_hx$/, '');
      const ti = find(uid, 'cdu.primaryInletT', '');
      if (ti && Number.isFinite(pc.inlet_K)) ti.keyframes[0].value = kToC(pc.inlet_K);
      const tf = find(uid, 'cdu.primaryFlow', '');
      if (tf && Number.isFinite(pc.flow_kg_s)) tf.keyframes[0].value = pc.flow_kg_s;
    }

    // Old per-model timeVarying curves.
    forEachModel(doc, (bucket, uid, model) => {
      const tv = model.timeVarying;
      if (!tv || typeof tv !== 'object') return;
      const params = Object.keys(tv).sort((x, y) => (x === 'heatOutput' ? -1 : y === 'heatOutput' ? 1 : 0));
      for (const param of params) {
        const points = (Array.isArray(tv[param]) ? tv[param] : [])
          .filter((p) => p && Number.isFinite(p.t) && Number.isFinite(p.value))
          .sort((x, y) => x.t - y.t);
        if (!points.length) continue;
        let targets = [];
        if (bucket === 'acus' && (param === 'airflow' || param === 'supplyTemp')) {
          const key = param === 'airflow' ? 'acu.airflow' : 'acu.supplyTemp';
          targets = tracks.filter((x) => x.modelUid === uid && x.param === key).map((trk) => ({ trk, scale: (v) => v }));
        } else if (bucket === 'boxes' && param === 'heatOutput') {
          targets = tracks.filter((x) => x.modelUid === uid && x.param === 'box.heat').map((trk) => ({ trk, scale: (v) => v }));
        } else if (bucket === 'racks' && param === 'heatOutput') {
          const servers = tracks.filter((x) => x.modelUid === uid && x.param === 'server.power');
          const h0 = points[0].value;
          targets = servers.map((trk) => {
            const p0 = trk.keyframes[0].value;
            return { trk, scale: h0 !== 0 ? (v) => round9(p0 * v / h0) : (v) => round9(v / servers.length) };
          });
          if (targets.length) report.push(`${uid}: rack heatOutput curve applied to its ${targets.length} servers in proportion to their power`);
        } else if (bucket === 'racks' && param.startsWith('power:')) {
          const trk = find(uid, 'server.power', param.slice(6));
          if (trk) targets = [{ trk, scale: (v) => v }];
        }
        if (!targets.length) { report.push(`${uid}: timeVarying "${param}" has no keyframable parameter here and was dropped`); continue; }
        for (const { trk, scale } of targets) {
          const kfs = keyframesFromPoints(points.map((p) => ({ t: p.t, value: scale(p.value) })));
          const field = trk.keyframes[0].value;
          if (kfs[0].value !== field) {
            // A curve only ever drove transient runs; a steady project's
            // boundary is its field, and opening it must not change that.
            if (mode === 'steady') {
              report.push(`${uid} ${trk.param} ${trk.target}: curve starts at ${kfs[0].value}, kept the steady value ${field} at t = 0`);
              kfs[0] = { t: 0, value: field };
            } else {
              report.push(`${uid} ${trk.param} ${trk.target}: start value ${field} → ${kfs[0].value} (from its curve)`);
            }
          }
          trk.keyframes = kfs;
        }
      }
      delete model.timeVarying;
    });

    // Liquid experiment: time-variable parts move to tracks; the block keeps
    // its solver role (dt, controller, parameter pack) and stays valid.
    let liquidScheduled = false;
    if (exp && typeof exp === 'object' && exp.settings && typeof exp.settings === 'object') {
      const s = exp.settings;
      if (s.loadStep === true && Number.isFinite(s.stepTime) && Number.isFinite(s.loadFactor)) {
        liquidScheduled = true;
        const heat = tracks.filter((x) => x.param === 'cp.heat');
        for (const trk of heat) upsertKeyframe(trk, s.stepTime, round9(trk.keyframes[0].value * s.loadFactor));
        report.push(`liquid load step ×${s.loadFactor} at ${s.stepTime} s → ${heat.length} cold-plate heat keyframes`);
        s.loadStep = false;
      }
      const ss = s.supplySchedule;
      if (ss && typeof ss === 'object' && ss.mode !== 'fixed') {
        liquidScheduled = true;
        for (const trk of tracks.filter((x) => x.param === 'cdu.primaryInletT')) {
          if (ss.mode === 'step') {
            trk.keyframes = [{ t: 0, value: kToC(ss.from) }, { t: ss.stepTime, value: kToC(ss.to), rampS: 0 }];
          } else if (ss.mode === 'ramp') {
            trk.keyframes = [{ t: 0, value: kToC(ss.from) }, { t: round9(ss.stepTime + ss.rampTime), value: kToC(ss.to), rampS: ss.rampTime }];
          } else if (ss.mode === 'sinusoid') {
            const dt = exp.dt_s > 0 ? exp.dt_s : 1;
            const n = Math.round((exp.duration_s || 0) / dt);
            const at = (t) => kToC(ss.mean + ss.amplitude * Math.sin(2 * Math.PI * t / ss.period));
            trk.keyframes = [{ t: 0, value: at(0) }];
            for (let k = 1; k <= n; k++) trk.keyframes.push({ t: round9(k * dt), value: at(k * dt), rampS: dt });
          }
        }
        report.push(`liquid supply schedule "${ss.mode}" → primary-inlet temperature keyframes${ss.mode === 'sinusoid' ? ' (sampled every dt as linear segments)' : ''}`);
        delete s.supplySchedule;
      }
    }
    // A liquid experiment without a DAE (.liquidrun v2) marches in time too; its
    // load step / supply schedule are keyframes now, so the timeline is transient.
    if (liquidScheduled && mode === 'steady') {
      mode = 'transient';
      report.push('the liquid run steps its inputs over time → the project timeline is transient');
    }

    // Timeline settings.
    let durationS = exp && Number.isFinite(exp.duration_s) && exp.duration_s > 0 ? exp.duration_s
      : (Number.isFinite(sim.endTimeSeconds) && sim.endTimeSeconds > 0 ? sim.endTimeSeconds : 600);
    const lastT = Math.max(0, ...tracks.map((x) => x.keyframes[x.keyframes.length - 1].t));
    if (lastT > durationS) { report.push(`timeline extended from ${durationS} s to ${lastT} s to hold every keyframe`); durationS = lastT; }
    const times = [];
    for (const trk of tracks) for (const f of trk.keyframes) { times.push(f.t); times.push(round9(f.t - (f.rampS || 0))); }
    let gridS = [1, 0.5, 0.1, 0.05, 0.01, 0.001].find((g) => times.every((x) => onGrid(x, g))) || 0.001;
    const dt = (liquidTransient || liquidScheduled) && exp.dt_s > 0 ? exp.dt_s : null;
    if (dt && times.every((x) => onGrid(x, dt)) && onGrid(durationS, dt)) gridS = dt;
    const roomT = Number((doc.geometry && doc.geometry.temperature) ?? 22);
    const init = dae && dae.analysis && dae.analysis.initialization;
    const airMode = sim.solverType === 'transient' ? 'transient' : 'steady';
    const liquidMode = liquidTransient || liquidScheduled ? 'transient' : 'steady';
    doc.transient = {
      mode, airMode, liquidMode, durationS, gridS,
      start: { kind: init === 'steady' ? 'steadyConverged' : 'uniform',
        uniform: { airT_C: Number.isFinite(roomT) ? roomT : 22, waterT_C: 30, solidT_C: 40 } },
      tracks,
    };
    stripTrackedFields(doc);
    doc.schemaVersion = 5;
    return { doc, report };
  }
  function migrateCfdprojV4toV5(raw) { return migrateCfdprojV4toV5WithReport(raw).doc; }
  function migrateCfdprojToV5(raw) { return migrateCfdprojV4toV5(raw); }

  const api = {
    EPS, registry, setRegistry, entryOf, enumerateTargets, getAt, setAt, deleteAt,
    valueAt, cornerTable, onGrid, validateTransient, defaultTransient, domainModes, withDomainModes, modeOfDomains,
    forEachModel, trackKey, snapshotTargets, tracksFromFields, materializeAt, stripTrackedFields,
    staticText, upsertKeyframe, commitEdit, syncFieldsToTracks,
    keyframesFromPoints, migrateCfdprojV4toV5WithReport, migrateCfdprojV4toV5, migrateCfdprojToV5,
  };
  if (typeof window !== 'undefined') {
    window.migrateCfdprojV4toV5 = migrateCfdprojV4toV5;
    window.migrateCfdprojToV5 = migrateCfdprojToV5;
  }
  if (typeof window !== 'undefined') window.cfdprojTransient = Object.assign(window.cfdprojTransient || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
