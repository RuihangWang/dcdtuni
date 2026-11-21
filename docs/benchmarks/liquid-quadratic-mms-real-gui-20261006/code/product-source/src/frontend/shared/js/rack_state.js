// rack_state.js — rack geometry state: defaults, normalisation, and the factory.
//
// Public API (window.rackState / module.exports):
//   configureIds({ nextModelId, nextPatchId })   <- REQUIRED before createRackModel
//   createRackModel(name), syncRackModelState(model), cloneRackState(raw)
//   createFacesState, getDefaultRackState, getDefaultEquipmentFaces, getDefaultCfd,
//   getDefaultModelTransform, getDefaultModelMoveTool, getRackEquipmentPreset,
//   _rackV3View, _rackServerView, _migrateOutletPatchToFaces,
//   FACE_CONFIGS, RACK_DEFAULT_UNIT_HEIGHT, RACK_EQUIPMENT_PRESETS
// Rack face ROLES (display only — see the block above FACE_CONFIGS):
//   rackFaceRole(faceId), rackFaceLabelKey(faceId), rackFaceColorLabelKey(faceId),
//   rackFaceLabel(faceId, t), RACK_SIDE_FLOW_ROLE
// Stacked servers (2026-09-17 spec §3-§4) add the depth model layer:
//   findServerDepthConflicts(model), setServerDepthAbsolute(model, uid, in, out),
//   defaultServerDepthOffsets(model), _serverDepthInterval(model, srv),
//   _clampServerDepthOffsets(in, out, depthM), SERVER_DEPTH_EPSILON_M
// All of them read a model and return data, except setServerDepthAbsolute,
// which writes the two fields it was given on the model it was handed and
// nothing else — no store, no undo, no scene, same as the rest of this file.
//
// `window.rackState` stays the canonical home, and the legacy page binds these
// names by destructuring it inside its own <script> — so a name added here is
// not reachable there until that destructure lists it. The stacked-server
// functions are therefore ALSO hung on window directly, which makes them
// callable from the page, from a browser-use `eval` and from an automation verb
// without touching STL_generate.html.
//
// Depends on: cfdproj_schema.js -> cfdproj_state_v4.js (+ cfdproj_migrate.js),
// resolved LAZILY at call time so script order beyond "before first use" does
// not matter.
//
// WHY THIS FILE EXISTS
// --------------------
// These 551 lines were lifted verbatim out of STL_generate.html so the React
// editor can create racks that are byte-identical to the ones the HTML editor
// creates. syncRackModelState alone is 190 lines that normalise slots, doors,
// servers and partitions against each other; a hand-port would be a SECOND
// definition of what a rack is, and the two would drift. A rack that is subtly
// wrong still looks right in the viewport and exports wrong STL patches — a
// defect that only surfaces as a bad solve.
//
// Every symbol here is a pure transform over its argument plus ECMAScript
// built-ins: no DOM, no editor state, no i18n, no Three.js. That is what makes
// the move safe, and it was verified by sweeping the extracted ranges for
// `state`, `uiState`, `window.`, `document.`, `alert(`, `t(` and `THREE`.
// (rackFaceLabel, added later, calls a translator it is HANDED — the caller's
// `t` — and reads no global one.)
//
// WHAT IS DELIBERATELY *NOT* HERE
// -------------------------------
// The id counters. `modelCounter` and `patchCounter` stay in STL_generate.html
// because they have writers and readers this module cannot see: the project
// load path ASSIGNS both (so ids continue past the ones in the opened file),
// and two patch-naming sites read `patchCounter` directly rather than going
// through nextPatchId(). Owning the counters here would sever those and hand
// out ids that collide with the loaded project. They are injected instead.

(function (root, factory) {
  const mod = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = mod;
  if (typeof window !== 'undefined') {
    window.rackState = mod;
    // Bare-window aliases for the stacked-server API only — see the header for
    // why. The underscore helpers stay module-scoped: they are internals, and a
    // global `_serverDepthInterval` is exactly the kind of name the legacy
    // page's 32k lines could already be using for something else.
    ['findServerDepthConflicts', 'setServerDepthAbsolute', 'defaultServerDepthOffsets']
      .forEach(function (name) { if (typeof mod[name] === 'function') window[name] = mod[name]; });
  }
})(this, function () {
  'use strict';

  // ---- injected id generators -------------------------------------------
  // See the header: the counters live with the editor, not here.
  function _idNotConfigured() {
    throw new Error(
      'rackState.configureIds({ nextModelId, nextPatchId }) must be called before ' +
      'createRackModel/cloneRackState — this module does not own the id counters.'
    );
  }
  let _nextModelId = _idNotConfigured;
  let _nextPatchId = _idNotConfigured;

  function configureIds(gen) {
    if (!gen || typeof gen.nextModelId !== 'function' || typeof gen.nextPatchId !== 'function') {
      throw new Error('configureIds needs { nextModelId(), nextPatchId() }');
    }
    _nextModelId = gen.nextModelId;
    _nextPatchId = gen.nextPatchId;
  }

  // ---- the .cfdproj format module, resolved lazily -----------------------
  // Lazily so this file can be loaded in any order relative to
  // cfdproj_state_v4.js; eagerly resolving would make script order a silent
  // trap (that module itself dereferences its schema at LOAD time, which is
  // exactly the failure this avoids reproducing).
  function _stateV4() {
    const mod = (typeof require !== 'undefined') ? require('./cfdproj_state_v4.js')
              : (typeof window !== 'undefined' ? window.cfdprojStateV4 : null);
    if (!mod || typeof mod.finalizeNewModelV4 !== 'function') {
      throw new Error('cfdproj_state_v4.js not available — load it before creating a rack');
    }
    return mod;
  }

  // ---- rack face roles (DISPLAY names of a rack's sides) ------------------
  // A rack's FRONT is where the air goes in and its BACK where it comes out.
  // Every exporter says so (geometry_pure.js names the front blanking panels
  // `<rack>_inlet_u18_u23` and the back ones `_outlet_`; a fan-server's front
  // is its `_inlet_patch`), and neither file format has an airflow field that
  // could say otherwise. So the editors name a rack side by that ROLE —
  // 进风面 / 出风面, Inlet / Outlet — and never by the geometric key, which is
  // what the user was shown in the right panel ("back" for the outlet).
  //
  // The role is keyed on the SIDE KEY, not on where the side ends up in the
  // room: rotating a rack turns both faces together, and the React toolbar's
  // mirror (a negative scale) moves the `front` side to the other end of the
  // rack while it stays `front` — still the inlet, in the label and in every
  // exported name alike, so the two cannot disagree.
  //
  // The keys `front`/`back` stay the DATA: deletedFaces.front, rackSlot.side,
  // the patch and solid names. Only the words change. v3 racks carried the
  // words as faces.front.displayName = 'inlet' (createFacesState below); the V4
  // migration keeps geometry.faces as an array of door entries only, so those
  // names were lost and every label site fell back to the raw key.
  //
  // `t` is passed in, so this module stays free of i18n.
  //
  // NOT the only place that encodes front=inlet / back=outlet. The exporters
  // spell it out themselves: geometry_pure.js getRackMergedSlotExportName
  // (`sideDisplayName`, the blanking-panel solid names), and the fan-server
  // inlet/outlet patches. The rack doors are keyed by role already
  // (`inletDoor`/`outletDoor` -> `_inlet_door`/`_outlet_door`, cfdrun_build.js).
  // An airflow reversal, if one is ever added, has to change all of them.
  const RACK_SIDE_FLOW_ROLE = Object.freeze({ front: 'inlet', back: 'outlet' });
  const _RACK_FACE_LABEL_KEYS = Object.freeze({
      inlet: 'label.inlet_face', outlet: 'label.outlet_face',
      top: 'label.face_top', bottom: 'label.face_bottom',
      left: 'label.face_left', right: 'label.face_right'
  });
  const _RACK_FACE_LABEL_FALLBACK = Object.freeze({
      inlet: 'Inlet', outlet: 'Outlet', top: 'Top', bottom: 'Bottom', left: 'Left', right: 'Right'
  });
  const _RACK_FACE_COLOR_KEYS = Object.freeze({ inlet: 'label.inlet_color', outlet: 'label.outlet_color' });

  function _own(table, key) {
      return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
  }

  /** 'front' -> 'inlet', 'back' -> 'outlet'; anything else (a role, 'top') unchanged. */
  function rackFaceRole(faceId) {
      const id = faceId == null ? '' : String(faceId);
      return _own(RACK_SIDE_FLOW_ROLE, id) || id;
  }

  /** The i18n key naming a rack face or server face, or null for an unknown id. */
  function rackFaceLabelKey(faceId) {
      return _own(_RACK_FACE_LABEL_KEYS, rackFaceRole(faceId)) || null;
  }

  /** The i18n key for the colour of an inlet/outlet face; null for the others. */
  function rackFaceColorLabelKey(faceId) {
      return _own(_RACK_FACE_COLOR_KEYS, rackFaceRole(faceId)) || null;
  }

  /**
   * The user-facing name of a rack side, U-slot side, server face or blanking
   * panel side. Translated through `t` when one is given and it knows the key;
   * otherwise the English role name, so a page without i18n still says Inlet.
   */
  function rackFaceLabel(faceId, t) {
      const key = rackFaceLabelKey(faceId);
      const text = key && typeof t === 'function' ? t(key) : null;
      if (typeof text === 'string' && text && text !== key) return text;
      const role = rackFaceRole(faceId);
      return _own(_RACK_FACE_LABEL_FALLBACK, role) || role;
  }

  const FACE_CONFIGS = {
      front:  { id: 'front', label: 'Front', wType: 'W', hType: 'H' },
      back:   { id: 'back',  label: 'Back',  wType: 'W', hType: 'H' },
      left:   { id: 'left',  label: 'Left',  wType: 'D', hType: 'H' },
      right:  { id: 'right', label: 'Right', wType: 'D', hType: 'H' },
      top:    { id: 'top',   label: 'Top',   wType: 'W', hType: 'D' },
      bottom: { id: 'bottom',label: 'Bottom',wType: 'W', hType: 'D' }
  };

  const RACK_DEFAULT_UNIT_HEIGHT = 0.04445;

  const RACK_EQUIPMENT_PRESETS = {
      fanServer: {
          label: 'fan-server',
          defaultName: 'fan-server',
          frontColor: '#d14b4b',
          backColor: '#9f3636'
      },
      nonFan: {
          label: 'non-fan equipment',
          defaultName: 'non-fan equipment',
          frontColor: '#4daafc',
          backColor: '#2f78c0'
      },
      liquidServer: {
          label: 'liquid-server',
          defaultName: 'liquid-server',
          frontColor: '#4dcc7a',
          backColor:  '#36994f'
      }
  };

  // Names the v3-shaped server view answers for that the V4 record does not
  // carry verbatim — the `has` trap needs them, because `Reflect.has` would say
  // no for an alias. `inletOffset`/`outletOffset` are top-level on the record
  // and would pass through anyway; they are listed (and cased in the proxy) so
  // the mapping is visible in one place. `opacity` is the one that truly needs
  // it: it lives at `meta.display.opacity` (spec §3.1). The retired
  // `extraInletOffset`/`extraOutletOffset` are deliberately NOT here — they are
  // migration input the normalizer deletes, not part of the view.
  const _RACK_SERVER_MAPPED = new Set([
      'id', 'name', 'kind', 'type', 'startUnit', 'spanUnits', 'liquidCooling',
      'inletOffset', 'outletOffset', 'opacity'
  ]);

  function createFacesState(labelOverrides = {}) {
      const faces = {};
      Object.keys(FACE_CONFIGS).forEach(faceId => {
          faces[faceId] = {
              ...FACE_CONFIGS[faceId],
              displayName: labelOverrides[faceId] || FACE_CONFIGS[faceId].label,
              prefixEnabled: true,
              opacity: 1,
              reverseSolidNormal: false,
              wallNames: [],
              wallOpacities: [],
              patches: []
          };
      });
      return faces;
  }

  function getDefaultRackState() {
      return {
          heightMode: 'totalHeight',
          unitCount: 42,
          unitHeight: RACK_DEFAULT_UNIT_HEIGHT,
          slotWidthRatio: 0.8,
          inletOffset: 0,
          outletOffset: 0,
          inletDoor: null,
          outletDoor: null,
          safeMarginTop: RACK_DEFAULT_UNIT_HEIGHT,      // meters, default = 1U height
          safeMarginBottom: RACK_DEFAULT_UNIT_HEIGHT,    // meters, default = 1U height
          defaultSpanUnits: 1,
          deletedFaces: { front: [], back: [] },
          throughSlotSigma: {},
          partitions: [],
          equipments: [],
          blankingPanelType: 'typicalSmallGap',
          coolingType: 'air',
          liquidManifold: {
              layout: 'split',
              mountFace: 'inlet',
              supplyPipe: { diameter: 0.025, offsetFromInlet: 0.10 },
              returnPipe: { diameter: 0.025, offsetFromInlet: 0.10 },
              pipeSpacing: 0.015
          }
      };
  }

  function getDefaultEquipmentFaces(equipment) {
      return {
          inlet:  { patches: [], color: equipment?.frontColor || '#4488ff' },
          outlet: { patches: [], color: equipment?.backColor  || '#44ff88' },
          top:    { patches: [] },
          bottom: { patches: [] },
          left:   { patches: [] },
          right:  { patches: [] }
      };
  }

  function _migrateOutletPatchToFaces(equipment, faces) {
      var op = equipment.outletPatch;
      if (!op) return;
      var patch = {
          id: _nextPatchId(),
          type: op.type === 'circle' ? 'circle' : 'rect',
          color: '#00cc66',
          applied: true
      };
      if (patch.type === 'rect') {
          var w = Number.isFinite(op.width)  ? op.width  : 0.6;
          var h = Number.isFinite(op.height) ? op.height : 0.8;
          var cx = Number.isFinite(op.cx) ? op.cx : 0.5;
          var cy = Number.isFinite(op.cy) ? op.cy : 0.5;
          patch.x = Math.max(0, cx - w / 2);
          patch.y = Math.max(0, cy - h / 2);
          patch.w = Math.min(w, 1 - patch.x);
          patch.h = Math.min(h, 1 - patch.y);
      } else {
          patch.cx = Number.isFinite(op.cx) ? op.cx : 0.5;
          patch.cy = Number.isFinite(op.cy) ? op.cy : 0.5;
          patch.r  = Number.isFinite(op.radius) ? op.radius : 0.3;
      }
      faces.outlet.patches.push(patch);
  }

  function getRackEquipmentPreset(kind) {
      return RACK_EQUIPMENT_PRESETS[kind] || RACK_EQUIPMENT_PRESETS.nonFan;
  }

  function cloneRackState(rawRack = {}) {
      const fallback = getDefaultRackState();
      const deletedFaces = rawRack.deletedFaces || {};
      return {
          heightMode: rawRack.heightMode === 'unitHeight' ? 'unitHeight' : fallback.heightMode,
          unitCount: Number.isFinite(rawRack.unitCount) ? rawRack.unitCount : fallback.unitCount,
          unitHeight: Number.isFinite(rawRack.unitHeight) ? rawRack.unitHeight : fallback.unitHeight,
          slotWidthRatio: Number.isFinite(rawRack.slotWidthRatio) ? rawRack.slotWidthRatio : fallback.slotWidthRatio,
          inletOffset: Number.isFinite(rawRack.inletOffset) ? rawRack.inletOffset : fallback.inletOffset,
          outletOffset: Number.isFinite(rawRack.outletOffset) ? rawRack.outletOffset : fallback.outletOffset,
          inletDoor: rawRack.inletDoor ? {
              // Legacy projects stored a fraction `porosity` (0..1) per door.
              // The 2026-04-16 unification replaces that with a `perforated`
              // sub-object identical to baffles/floors. We don't transform
              // porosity here — normalizeState scrubs the legacy field on
              // load and the migration script rewrites cfdproj files.
              perforated: (rawRack.inletDoor.perforated && typeof rawRack.inletDoor.perforated === 'object')
                  ? {
                      shape: rawRack.inletDoor.perforated.shape || 'circle',
                      holeDiameterMm: Number.isFinite(rawRack.inletDoor.perforated.holeDiameterMm) ? rawRack.inletDoor.perforated.holeDiameterMm : 3.0,
                      openAreaPct: Number.isFinite(rawRack.inletDoor.perforated.openAreaPct) ? rawRack.inletDoor.perforated.openAreaPct : 64.0,
                  }
                  : { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 },
              renderDoor: rawRack.inletDoor.renderDoor !== false,
          } : null,
          outletDoor: rawRack.outletDoor ? {
              perforated: (rawRack.outletDoor.perforated && typeof rawRack.outletDoor.perforated === 'object')
                  ? {
                      shape: rawRack.outletDoor.perforated.shape || 'circle',
                      holeDiameterMm: Number.isFinite(rawRack.outletDoor.perforated.holeDiameterMm) ? rawRack.outletDoor.perforated.holeDiameterMm : 3.0,
                      openAreaPct: Number.isFinite(rawRack.outletDoor.perforated.openAreaPct) ? rawRack.outletDoor.perforated.openAreaPct : 64.0,
                  }
                  : { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 },
              renderDoor: rawRack.outletDoor.renderDoor !== false,
          } : null,
          safeMarginTop: (function() {
              if (Number.isFinite(rawRack.safeMarginTop)) return rawRack.safeMarginTop;
              if (Number.isFinite(rawRack.safeMarginTopUnits)) {
                  var uH = Number.isFinite(rawRack.unitHeight) ? rawRack.unitHeight : RACK_DEFAULT_UNIT_HEIGHT;
                  return rawRack.safeMarginTopUnits * uH;
              }
              return fallback.safeMarginTop;
          })(),
          safeMarginBottom: (function() {
              if (Number.isFinite(rawRack.safeMarginBottom)) return rawRack.safeMarginBottom;
              if (Number.isFinite(rawRack.safeMarginBottomUnits)) {
                  var uH = Number.isFinite(rawRack.unitHeight) ? rawRack.unitHeight : RACK_DEFAULT_UNIT_HEIGHT;
                  return rawRack.safeMarginBottomUnits * uH;
              }
              return fallback.safeMarginBottom;
          })(),
          defaultSpanUnits: Number.isFinite(rawRack.defaultSpanUnits) ? rawRack.defaultSpanUnits : fallback.defaultSpanUnits,
          deletedFaces: {
              front: Array.isArray(deletedFaces.front) ? deletedFaces.front.map(Boolean) : [],
              back: Array.isArray(deletedFaces.back) ? deletedFaces.back.map(Boolean) : []
          },
          throughSlotSigma: (rawRack.throughSlotSigma && typeof rawRack.throughSlotSigma === 'object')
              ? Object.fromEntries(Object.entries(rawRack.throughSlotSigma).map(([k, v]) => [k, Number.isFinite(v) ? v : 0.05]))
              : {},
          partitions: Array.isArray(rawRack.partitions)
              ? rawRack.partitions.map((partition, index) => ({
                  index: Number.isFinite(partition.index) ? partition.index : index,
                  name: partition.name || `partition_${index + 1}`,
                  // Default: deleted=true (hidden). Preserve explicit deleted flag; legacy forceVisible
                  // maps to deleted=false (visible) for migration of older project files.
                  deleted: typeof partition.deleted === 'boolean'
                      ? partition.deleted
                      : (partition.forceVisible ? false : true)
              }))
              : [],
          equipments: Array.isArray(rawRack.equipments)
              ? rawRack.equipments.map((equipment, index) => {
                  const preset = getRackEquipmentPreset(equipment.kind);
                  const rawFaces = equipment.faces;
                  var faces;
                  if (rawFaces && typeof rawFaces === 'object' && rawFaces.inlet) {
                      // New format — clone faces
                      faces = {};
                      ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'].forEach(function(fid) {
                          var src = rawFaces[fid] || {};
                          faces[fid] = {
                              patches: Array.isArray(src.patches) ? src.patches.map(function(p) {
                                  return Object.assign({}, p, { id: p.id || _nextPatchId() });
                              }) : [],
                          };
                          if (src.color) faces[fid].color = src.color;
                      });
                  } else {
                      // Old format — migrate
                      faces = getDefaultEquipmentFaces(equipment);
                      _migrateOutletPatchToFaces(equipment, faces);
                  }
                  return {
                      id: equipment.id || `e_raw_${index + 1}`,
                      kind: equipment.kind === 'fanServer' ? 'fanServer' : equipment.kind === 'liquidServer' ? 'liquidServer' : 'nonFan',
                      name: equipment.name || `${preset.defaultName}_${index + 1}`,
                      startUnit: Number.isFinite(equipment.startUnit) ? equipment.startUnit : 0,
                      spanUnits: Number.isFinite(equipment.spanUnits) ? equipment.spanUnits : 1,
                      extraInletOffset: Number.isFinite(equipment.extraInletOffset) ? equipment.extraInletOffset : 0,
                      extraOutletOffset: Number.isFinite(equipment.extraOutletOffset) ? equipment.extraOutletOffset : 0,
                      faces: faces,
                      liquidCooling: equipment.liquidCooling
                          ? JSON.parse(JSON.stringify(equipment.liquidCooling)) : null
                  };
              })
              : [],
          blankingPanelType: rawRack.blankingPanelType || fallback.blankingPanelType,
          coolingType: (rawRack.coolingType === 'liquid' || rawRack.coolingType === 'air') ? rawRack.coolingType : fallback.coolingType,
          liquidManifold: (rawRack.liquidManifold && typeof rawRack.liquidManifold === 'object') ? {
              layout: rawRack.liquidManifold.layout || fallback.liquidManifold.layout,
              mountFace: rawRack.liquidManifold.mountFace === 'outlet' ? 'outlet' : (fallback.liquidManifold.mountFace || 'inlet'),
              supplyPipe: (rawRack.liquidManifold.supplyPipe && typeof rawRack.liquidManifold.supplyPipe === 'object') ? {
                  diameter: Number.isFinite(rawRack.liquidManifold.supplyPipe.diameter) ? rawRack.liquidManifold.supplyPipe.diameter : fallback.liquidManifold.supplyPipe.diameter,
                  offsetFromInlet: Number.isFinite(rawRack.liquidManifold.supplyPipe.offsetFromInlet) ? rawRack.liquidManifold.supplyPipe.offsetFromInlet : fallback.liquidManifold.supplyPipe.offsetFromInlet
              } : { ...fallback.liquidManifold.supplyPipe },
              returnPipe: (rawRack.liquidManifold.returnPipe && typeof rawRack.liquidManifold.returnPipe === 'object') ? {
                  diameter: Number.isFinite(rawRack.liquidManifold.returnPipe.diameter) ? rawRack.liquidManifold.returnPipe.diameter : fallback.liquidManifold.returnPipe.diameter,
                  offsetFromInlet: Number.isFinite(rawRack.liquidManifold.returnPipe.offsetFromInlet) ? rawRack.liquidManifold.returnPipe.offsetFromInlet : fallback.liquidManifold.returnPipe.offsetFromInlet
              } : { ...fallback.liquidManifold.returnPipe },
              pipeSpacing: Number.isFinite(rawRack.liquidManifold.pipeSpacing) ? rawRack.liquidManifold.pipeSpacing : fallback.liquidManifold.pipeSpacing
          } : { ...fallback.liquidManifold, supplyPipe: { ...fallback.liquidManifold.supplyPipe }, returnPipe: { ...fallback.liquidManifold.returnPipe } }
      };
  }

  function _rackV3View(model) {
      const geom = model.geometry || {};
      const cool = model.cooling || {};
      const faces = Array.isArray(geom.faces) ? geom.faces : [];
      const doorView = (uid) => {
          const f = faces.find(x => x && x.uid === uid && x.role === 'door');
          if (!f) return null;
          const out = {};
          for (const k of Object.keys(f)) {
              if (k === 'uid' || k === 'role') continue;
              if (k === 'perforation') out.perforated = f.perforation; else out[k] = f[k];
          }
          return out;
      };
      const v = {
          unitCount: geom.slot,
          unitHeight: geom.slotHeight,
          slotWidthRatio: geom.slotWidthRatio,
          coolingType: cool.type,
          liquidManifold: cool.liquidManifold,
          blankingPanelType: cool.blankingPanelType,
          inletDoor: doorView('inletDoor'),
          outletDoor: doorView('outletDoor'),
      };
      // Top-level v3 fields cloneRackState also reads (heightMode, inletOffset, outletOffset,
      // safeMargin*, defaultSpanUnits, throughSlotSigma, deletedFaces) live directly on model.
      for (const k of ['heightMode', 'inletOffset', 'outletOffset', 'safeMarginTop', 'safeMarginTopUnits',
                       'safeMarginBottom', 'safeMarginBottomUnits', 'defaultSpanUnits', 'throughSlotSigma', 'deletedFaces']) {
          if (k in model) v[k] = model[k];
      }
      return v;
  }

  function _rackServerView(servers, uid) {
      const srv = servers ? servers[uid] : null;
      if (!srv) return null;
      const g = () => { if (!srv.geometry) srv.geometry = {}; return srv.geometry; };
      const me = () => { if (!srv.meta) srv.meta = {}; return srv.meta; };
      const disp = () => { const m = me(); if (!m.display || typeof m.display !== 'object') m.display = {}; return m.display; };
      return new Proxy(srv, {
          get(target, prop) {
              switch (prop) {
                  case 'id': return uid;
                  case 'name': return (target.meta || {}).name;
                  case 'kind': return target.type;
                  case 'type': return target.type;
                  case 'startUnit': return (target.geometry || {}).slotPosition;
                  case 'spanUnits': return (target.geometry || {}).slotOccupation;
                  case 'liquidCooling': return target.liquidCooling;
                  // Absolute depth setbacks (spec §3.1): metres back from the rack
                  // inlet plane and from the rack outlet plane. They are top-level
                  // on the record, so the default branch would already serve them;
                  // they are cased to keep both directions in one place.
                  case 'inletOffset': return target.inletOffset;
                  case 'outletOffset': return target.outletOffset;
                  // Server opacity has exactly ONE storage (spec §3.1). Read
                  // without creating the display block: a `get` that writes is a
                  // surprise in a render loop.
                  case 'opacity': return (((target.meta || {}).display) || {}).opacity;
                  default: return Reflect.get(target, prop);
              }
          },
          set(target, prop, value) {
              switch (prop) {
                  case 'id': return true; // uid is the dict key; read-only here
                  case 'name': me().name = value; return true;
                  case 'kind': target.type = value; return true;
                  case 'type': target.type = value; return true;
                  case 'startUnit': g().slotPosition = value; return true;
                  case 'spanUnits': g().slotOccupation = value; return true;
                  case 'liquidCooling': target.liquidCooling = value; return true;
                  case 'inletOffset': target.inletOffset = value; return true;
                  case 'outletOffset': target.outletOffset = value; return true;
                  case 'opacity': disp().opacity = value; return true;
                  default: return Reflect.set(target, prop, value);
              }
          },
          has(target, prop) { return _RACK_SERVER_MAPPED.has(prop) || Reflect.has(target, prop); },
      });
  }

  // ---- stacked servers: absolute depth and the conflict predicate ---------
  // Spec docs/superpowers/specs/2026-09-17-rack-stacked-servers-design.md §3-§4.
  //
  // A U-slot no longer holds exactly one server. Two records may share U's when
  // they sit at different depths — a 5U fan server recessed from the outlet and
  // a 1U switch near the inlet, both at U16 — so occupancy became a per-slot
  // list of depth intervals, and "does this fit" became a pure predicate.
  //
  // Everything below reads a model and returns data: no scene, no store, no
  // undo. The normalizer, the panels' dry runs and the automation contract all
  // have to answer the same question the same way, and a second copy of the
  // predicate is how they would stop doing that.

  /** Two boxes whose faces touch are a legal stack; the overlap must beat this. */
  const SERVER_DEPTH_EPSILON_M = 1e-6;

  /**
   * The rack's over-depth guard, applied per server: the two setbacks together
   * may not eat more than 90% of the rack depth. The same fraction the
   * rack-level offsets use, so a server can never be deeper than its own row.
   */
  const SERVER_DEPTH_MAX_FRACTION = 0.9;

  /** Rack depth D in metres. Rack-local Z runs inlet face 0 -> outlet face D. */
  function _rackDepthM(model) {
      const size = (model && model.geometry) ? model.geometry.size : null;
      const depth = Number(size && size.z);
      return Number.isFinite(depth) && depth > 0 ? depth : 0;
  }

  /**
   * Clamp one server's absolute setbacks (spec §3.1):
   *
   *     0 <= inletOffset ; 0 <= outletOffset ; inlet + outlet <= 0.9 * D
   *
   * Over budget, BOTH are scaled by the same factor — the style the rack-level
   * clamp uses — so the server keeps its place within the row instead of being
   * shoved against one face.
   */
  function _clampServerDepthOffsets(inletOffset, outletOffset, depthM) {
      const inlet = Math.max(0, Number(inletOffset) || 0);
      const outlet = Math.max(0, Number(outletOffset) || 0);
      const depth = Number(depthM) || 0;
      const total = inlet + outlet;
      if (depth > 0 && total > depth * SERVER_DEPTH_MAX_FRACTION) {
          const scale = (depth * SERVER_DEPTH_MAX_FRACTION) / total;
          return { inletOffset: inlet * scale, outletOffset: outlet * scale };
      }
      return { inletOffset: inlet, outletOffset: outlet };
  }

  /**
   * One server's depth interval as { inletOffset, outletOffset, z0, z1 }, read
   * WITHOUT writing to the record.
   *
   * z0/z1 are rack-local Z: z0 = inletOffset, z1 = D - outletOffset. When the
   * absolute fields are missing — a document saved before they existed — they
   * are derived from the retired rack-relative extras exactly the way the
   * normalizer's backfill does (spec §3.2), so a reader and the normalizer can
   * never disagree about where an old server sits.
   */
  function _serverDepthInterval(model, srv) {
      const depth = _rackDepthM(model);
      let inlet = Number(srv && srv.inletOffset);
      let outlet = Number(srv && srv.outletOffset);
      if (!Number.isFinite(inlet)) {
          inlet = (Number(model && model.inletOffset) || 0) + (Number(srv && srv.extraInletOffset) || 0);
      }
      if (!Number.isFinite(outlet)) {
          outlet = (Number(model && model.outletOffset) || 0) + (Number(srv && srv.extraOutletOffset) || 0);
      }
      const clamped = _clampServerDepthOffsets(inlet, outlet, depth);
      return {
          inletOffset: clamped.inletOffset,
          outletOffset: clamped.outletOffset,
          z0: clamped.inletOffset,
          z1: Math.max(clamped.inletOffset, depth - clamped.outletOffset)
      };
  }

  /**
   * One server's U range as { startUnit, spanUnits }, read WITHOUT writing to
   * the record. The clamps are the normalizer's, so the predicate sees the range
   * a sync would keep rather than the one a hand-edited file claims.
   */
  function _serverSlotSpan(model, srv) {
      const geometry = (srv && srv.geometry) || {};
      const slots = Math.max(0, Math.round(Number(model && model.geometry ? model.geometry.slot : 0) || 0));
      const startUnit = Math.max(0, Math.min(Math.max(0, slots - 1), Math.round(Number(geometry.slotPosition) || 0)));
      const spanUnits = Math.max(1, Math.min(Math.max(1, slots - startUnit), Math.round(Number(geometry.slotOccupation) || 1)));
      return { startUnit: startUnit, spanUnits: spanUnits };
  }

  /** A record's identity: its own uid wins, the dict key is the fallback. */
  function _serverUid(srv, key) {
      return (srv && srv.uid != null) ? String(srv.uid) : String(key);
  }

  /** The rack's uid-keyed server dict, or null when there is nothing to place. */
  function _serversDictOf(model) {
      if (!model || model.kind !== 'rack') return null;
      const cons = model.constructions;
      return (cons && cons.servers && typeof cons.servers === 'object') ? cons.servers : null;
  }

  /** Placement order: bottom-up, and within one U the server nearest the inlet. */
  function _bySlotThenInlet(a, b) {
      return (a.startUnit - b.startUnit) || (a.z0 - b.z0);
  }

  /**
   * Walk already-normalised placement records and keep the ones that fit.
   *
   * A record is dropped only when, on some slot it covers, its depth interval
   * intersects an ALREADY PLACED one by more than the epsilon (spec D6) — two
   * boxes meeting face to face are a legal stack, and so are two boxes sharing
   * depth in different U's.
   *
   * Returns { kept: [uid...], conflicts: [{ aUid, bUid, slots, overlapM }] }.
   * `aUid` is the survivor (placed first), `bUid` the record dropped because of
   * it; `slots` lists every shared slot where the two interpenetrate and
   * `overlapM` is the deepest such intersection. Diagnostics only — no geometry
   * reads this, the panels' badge does.
   */
  function _resolveServerPlacement(slotCount, records) {
      const placed = [];
      const count = Math.max(0, Math.round(Number(slotCount) || 0));
      for (let i = 0; i < count; i++) placed.push([]);
      const kept = [];
      const conflicts = [];
      records.forEach(rec => {
          const hits = [];
          for (let offset = 0; offset < rec.spanUnits; offset++) {
              const slot = rec.startUnit + offset;
              const occupants = placed[slot];
              if (!occupants) continue;
              occupants.forEach(other => {
                  const overlapM = Math.min(rec.z1, other.z1) - Math.max(rec.z0, other.z0);
                  if (overlapM <= SERVER_DEPTH_EPSILON_M) return;  // touching faces stack
                  // One entry per PAIR, listing every shared slot: a 2U switch
                  // interpenetrating a 5U server is one conflict, not two.
                  let hit = null;
                  for (const h of hits) { if (h.aUid === other.uid) { hit = h; break; } }
                  if (!hit) {
                      hit = { aUid: other.uid, bUid: rec.uid, slots: [], overlapM: overlapM };
                      hits.push(hit);
                  }
                  hit.slots.push(slot);
                  hit.overlapM = Math.max(hit.overlapM, overlapM);
              });
          }
          if (hits.length) {
              hits.forEach(h => conflicts.push(h));
              return;
          }
          for (let offset = 0; offset < rec.spanUnits; offset++) {
              const slot = rec.startUnit + offset;
              if (!placed[slot]) placed[slot] = [];
              placed[slot].push({ uid: rec.uid, z0: rec.z0, z1: rec.z1 });
          }
          kept.push(rec.uid);
      });
      return { kept: kept, conflicts: conflicts };
  }

  /**
   * Placement records for a list of { key, srv } pairs, in placement order.
   * Reads the raw records; writes nothing.
   */
  function _serverPlacementRecords(model, items) {
      return items
          .map(item => {
              const span = _serverSlotSpan(model, item.srv);
              const depth = _serverDepthInterval(model, item.srv);
              return {
                  uid: _serverUid(item.srv, item.key),
                  startUnit: span.startUnit,
                  spanUnits: span.spanUnits,
                  z0: depth.z0,
                  z1: depth.z1
              };
          })
          .sort(_bySlotThenInlet);
  }

  /**
   * Every depth conflict in a rack: [{ aUid, bUid, slots, overlapM }].
   *
   * Pure — the model is not touched — and the same walk the normalizer runs, so
   * an empty result here means a sync would keep every record. That is what
   * makes it usable as a pre-write dry run (spec §4.3): the add-server panel
   * asks "what if I added this", and `setServerDepthAbsolute` asks "what if this
   * server moved there", both without mutating anything.
   */
  function findServerDepthConflicts(model) {
      const servers = _serversDictOf(model);
      if (!servers) return [];
      const items = Object.keys(servers)
          .map(key => ({ key: key, srv: servers[key] }))
          .filter(item => item.srv && typeof item.srv === 'object');
      if (items.length < 2) return [];
      const slotCount = Number(model.geometry ? model.geometry.slot : 0) || 0;
      return _resolveServerPlacement(slotCount, _serverPlacementRecords(model, items)).conflicts;
  }

  /**
   * The depth a NEW server gets when the caller has no opinion: the rack's own
   * row plane. Spec §7.1 — an empty depth box in an add-server panel means
   * "where the row is", which is what creating a server did before the absolute
   * fields existed.
   */
  function defaultServerDepthOffsets(model) {
      return {
          inletOffset: Number(model && model.inletOffset) || 0,
          outletOffset: Number(model && model.outletOffset) || 0
      };
  }

  /**
   * Write one server's ABSOLUTE setbacks, in place, on the model handed in.
   *
   * The caller owns the transaction: this is meant to run inside a store draft
   * (or the legacy's live-mutate path), so it does not push an undo checkpoint,
   * does not call store.edit and does not re-run syncRackModelState — the edit
   * wrapper that supplied the draft does that, and `serverConflicts` is rebuilt
   * there.
   *
   * Refusals, none of which writes anything:
   *   { ok: false, error: 'range' }   the pair breaks §3.1 (negative, over 90% of
   *                                   D, or not a number). Refused rather than
   *                                   silently clamped: the panel shows the number
   *                                   the user typed, and a quiet clamp would leave
   *                                   the box and the model disagreeing.
   *   { ok: false, error: 'overlap' } the new interval intersects another server
   *                                   covering the same U's (dry run of
   *                                   findServerDepthConflicts with the new value).
   *   { ok: false, error: 'missing' } no such rack or no such server.
   */
  function setServerDepthAbsolute(model, serverUid, inletOffset, outletOffset) {
      const servers = _serversDictOf(model);
      if (!servers) return { ok: false, error: 'missing' };
      const wanted = String(serverUid);
      const key = Object.keys(servers).find(k => _serverUid(servers[k], k) === wanted);
      const srv = (key != null && servers[key] && typeof servers[key] === 'object') ? servers[key] : null;
      if (!srv) return { ok: false, error: 'missing' };
      if (!Number.isFinite(inletOffset) || !Number.isFinite(outletOffset)) {
          return { ok: false, error: 'range' };
      }
      const clamped = _clampServerDepthOffsets(inletOffset, outletOffset, _rackDepthM(model));
      if (clamped.inletOffset !== inletOffset || clamped.outletOffset !== outletOffset) {
          return { ok: false, error: 'range' };
      }
      // Dry run: a throwaway rack shape carrying the NEW value, judged by the
      // same predicate the panels and the normalizer use. The real dict is left
      // alone, so a refusal cannot half-write the draft.
      const probe = {};
      Object.keys(servers).forEach(k => {
          const rec = servers[k];
          if (!rec || typeof rec !== 'object') return;
          const uid = _serverUid(rec, k);
          probe[uid] = (uid === wanted)
              ? Object.assign({}, rec, { inletOffset: clamped.inletOffset, outletOffset: clamped.outletOffset })
              : rec;
      });
      const dryRun = findServerDepthConflicts({
          kind: 'rack',
          geometry: model.geometry,
          inletOffset: model.inletOffset,
          outletOffset: model.outletOffset,
          constructions: { servers: probe }
      });
      if (dryRun.length) return { ok: false, error: 'overlap' };
      srv.inletOffset = clamped.inletOffset;
      srv.outletOffset = clamped.outletOffset;
      // The retired rack-relative pair must not survive a write: a stale extra
      // next to an absolute field is exactly the ambiguity §3.1 removes.
      delete srv.extraInletOffset;
      delete srv.extraOutletOffset;
      return { ok: true };
  }

  function syncRackModelState(model) {
      if (!model || model.kind !== 'rack') return;

      // cloneRackState reads a v3-shaped rack config (built V4-natively by _rackV3View from
      // the live V4 fields) and returns a fully-normalized v3-shaped config. We scatter its
      // fields into their V4 homes in place (geometry.slot, cooling.type, top-level, doors on
      // geometry.faces). Servers are normalized separately below (NOT via this clone).
      const rackClone = cloneRackState(_rackV3View(model));
      const geom = model.geometry;
      const cool = model.cooling || (model.cooling = {});
      // Scatter the normalized scalar fields to their V4 homes.
      geom.slot = rackClone.unitCount;
      geom.slotHeight = rackClone.unitHeight;
      geom.slotWidthRatio = rackClone.slotWidthRatio;
      cool.type = rackClone.coolingType || 'air';
      if (rackClone.liquidManifold) cool.liquidManifold = rackClone.liquidManifold;
      cool.blankingPanelType = rackClone.blankingPanelType;
      model.heightMode = rackClone.heightMode;
      model.inletOffset = rackClone.inletOffset;
      model.outletOffset = rackClone.outletOffset;
      model.safeMarginTop = rackClone.safeMarginTop;
      model.safeMarginBottom = rackClone.safeMarginBottom;
      model.defaultSpanUnits = rackClone.defaultSpanUnits;
      model.throughSlotSigma = rackClone.throughSlotSigma || {};
      // Doors: rebuild the geometry.faces door entries from the normalized clone
      // (perforated -> perforation), preserving any non-door face entries.
      var _existingFaces = Array.isArray(geom.faces) ? geom.faces : [];
      var _nonDoorFaces = _existingFaces.filter(function(f){ return !(f && f.role === 'door' && (f.uid === 'inletDoor' || f.uid === 'outletDoor')); });
      [['inletDoor', rackClone.inletDoor], ['outletDoor', rackClone.outletDoor]].forEach(function(pair){
          var d = pair[1];
          if (!d) return;
          var entry = { uid: pair[0], role: 'door' };
          for (var k in d) { if (k === 'perforated') entry.perforation = d.perforated; else entry[k] = d[k]; }
          _nonDoorFaces.push(entry);
      });
      geom.faces = _nonDoorFaces;
      geom.slot = Math.max(3, Math.round(geom.slot || 42));
      geom.slotHeight = Math.max(0.01, Number(geom.slotHeight) || RACK_DEFAULT_UNIT_HEIGHT);
      geom.slotWidthRatio = Math.min(0.95, Math.max(0.2, Number(geom.slotWidthRatio) || 0.8));
      model.inletOffset = Math.max(0, Number(model.inletOffset) || 0);
      model.outletOffset = Math.max(0, Number(model.outletOffset) || 0);
      var halfD = model.geometry.size.z * 0.5;
      model.inletOffset = Math.min(model.inletOffset, halfD);
      model.outletOffset = Math.min(model.outletOffset, halfD);
      if (model.inletOffset + model.outletOffset >= model.geometry.size.z) {
          var scale = (model.geometry.size.z * 0.9) / (model.inletOffset + model.outletOffset);
          model.inletOffset *= scale;
          model.outletOffset *= scale;
      }
      // Door perforation block clamping: keep openAreaPct in (0.1, 99.9).
      // (The legacy porosity fraction has been replaced by a full
      // `perforation` sub-object, identical to baffles/floors.)
      // V4 doors live as geometry.faces entries with uid:'inletDoor'/'outletDoor'.
      ['inletDoor', 'outletDoor'].forEach(function(uid) {
          var door = geom.faces.find(function(f){ return f && f.uid === uid && f.role === 'door'; });
          if (!door) return;
          if (!door.perforation || typeof door.perforation !== 'object') {
              door.perforation = { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };
          }
          var p = door.perforation;
          if (!p.shape || typeof p.shape !== 'string') p.shape = 'circle';
          p.holeDiameterMm = Math.max(0.1, Number(p.holeDiameterMm) || 3.0);
          p.openAreaPct = Math.min(99.9, Math.max(0.1, Number(p.openAreaPct) || 64.0));
      });
      model.safeMarginTop = Math.max(0, Number(model.safeMarginTop) || 0);
      model.safeMarginBottom = Math.max(0, Number(model.safeMarginBottom) || 0);
      var slotAreaHeight = geom.slot * geom.slotHeight;
      var maxMarginSum = slotAreaHeight * 0.5;
      if (model.safeMarginTop + model.safeMarginBottom > maxMarginSum) {
          var mScale = maxMarginSum / (model.safeMarginTop + model.safeMarginBottom);
          model.safeMarginTop *= mScale;
          model.safeMarginBottom *= mScale;
      }
      model.defaultSpanUnits = Math.max(1, Math.min(geom.slot, Math.round(model.defaultSpanUnits || 1)));

      // 2026-04-18 Y-up: base.H is the rack's vertical extent along +Y.
      if (model.heightMode === 'unitHeight') {
          // User controls unitHeight + unitCount → auto-compute total H
          model.geometry.size.y = geom.slot * geom.slotHeight + model.safeMarginTop + model.safeMarginBottom;
      } else {
          // totalHeight mode: user controls H + unitCount → auto-compute unitHeight
          model.geometry.size.y = Math.max(model.geometry.size.y || geom.slot * geom.slotHeight, geom.slot * 0.01);
          var marginSum = model.safeMarginTop + model.safeMarginBottom;
          geom.slotHeight = (model.geometry.size.y - marginSum) / geom.slot;
      }

      // Force-disable doors if no slot area height (drop the door face entries).
      var slotAreaH = geom.slot * geom.slotHeight;
      if (slotAreaH <= 1e-6) {
          geom.faces = geom.faces.filter(function(f){ return !(f && f.role === 'door' && (f.uid === 'inletDoor' || f.uid === 'outletDoor')); });
      }

      model.geometry.size.x = Math.max(0.1, model.geometry.size.x || 0.6);
      model.geometry.size.z = Math.max(0.1, model.geometry.size.z || 1.0);
      // Racks carry no custom mesh: reset the flat legacy custom-mesh fields to
      // their native top-level defaults (Object.assign keeps the same flat shape
      // a rack already holds; racks never get the customMesh nested home).
      Object.assign(model, { customSTLBase64: null, customOffset: { x: 0, y: 0, z: 0 }, solidRegions: [] });

      // V4 racks store geometry.faces as an array of door entries (the
      // front=inlet / back=outlet display naming is `rackFaceLabel`, above), so
      // the legacy object-shape {front,back} displayName fix-up only applies
      // when faces is still a v3 object. Skip it for the V4 array form.
      const faces = model.geometry.faces || createFacesState({ front: 'inlet', back: 'outlet' });
      model.geometry.faces = faces;
      if (faces && !Array.isArray(faces) && faces.front && faces.back) {
          if (!faces.front.displayName || faces.front.displayName === FACE_CONFIGS.front.label) faces.front.displayName = 'inlet';
          if (!faces.back.displayName || faces.back.displayName === FACE_CONFIGS.back.label) faces.back.displayName = 'outlet';
      }

      // Servers live V4-native in model.constructions.servers ({uid: server}).
      // Normalize in place while PRESERVING each server's full object — cooling
      // (physics), constructions, meta, liquidCooling — by mutating the live
      // servers and rebuilding the dict in placement order. We must NOT
      // round-trip through the v3 rack equipments proxy here: serverStore
      // reshaping would reset server.cooling to {} and wipe per-server physics.
      //
      // Two passes, because a record's own fields have to be settled before it
      // can be compared with its neighbours: pass 1 migrates and clamps every
      // record (nothing is dropped yet), pass 2 places them. Placement is no
      // longer "one server per U" (spec §4.2): a slot holds a LIST of depth
      // intervals, and a record is dropped only when it interpenetrates one
      // already placed there — so two servers may share a U range at different
      // depths. A drop is reported in constructions.serverConflicts instead of
      // being silent.
      const cons0 = model.constructions || (model.constructions = {});
      if (!cons0.servers || typeof cons0.servers !== 'object') cons0.servers = {};
      const _depthM = _rackDepthM(model);
      // Spec §3.2: an old document has no per-server opacity, so every server
      // inherits the rack's CURRENT value and the project opens looking exactly
      // like it did before the two knobs were split.
      const _rackOpacity = (model.meta && model.meta.display && Number.isFinite(Number(model.meta.display.opacity)))
          ? Number(model.meta.display.opacity)
          : 1;
      const _serverEntries = Object.keys(cons0.servers)
          .map(uid => cons0.servers[uid])
          .filter(Boolean)
          .map(srv => { if (!srv.geometry) srv.geometry = {}; return srv; })
          .sort((a, b) => (a.geometry.slotPosition || 0) - (b.geometry.slotPosition || 0));
      const _byUid = {};
      _serverEntries.forEach((srv, index) => {
          const uid = srv.uid != null ? String(srv.uid) : `e_sync_${index + 1}`;
          srv.uid = uid;
          const startUnit = Math.max(0, Math.min(geom.slot - 1, Math.round(srv.geometry.slotPosition || 0)));
          const spanUnits = Math.max(1, Math.min(geom.slot - startUnit, Math.round(srv.geometry.slotOccupation || 1)));
          srv.type = srv.type === 'fanServer' ? 'fanServer' : srv.type === 'liquidServer' ? 'liquidServer' : 'nonFan';
          if (!srv.meta) srv.meta = {};
          if (srv.meta.name == null) srv.meta.name = uid;
          srv.geometry.slotPosition = startUnit;
          srv.geometry.slotOccupation = spanUnits;
          if (!srv.faces) srv.faces = getDefaultEquipmentFaces(_rackServerView(cons0.servers, uid));
          if (srv.liquidCooling == null) srv.liquidCooling = null;
          // Per-server depth, ABSOLUTE (spec §3.1): inletOffset is the setback
          // from the rack inlet plane, outletOffset from the rack outlet plane,
          // so a server may sit closer to a door than the rack-wide inset does
          // (its outlet 2 cm from the rack's outlet face while the row is inset
          // 20 cm) and the row sliders cannot move it any more.
          //
          // Backfill only when the absolute field is missing or not a number:
          // that is a document from before the fields existed, whose depth is
          // still spelled as the rack-relative extras. The extras are then
          // deleted unconditionally — they are migration input, not storage, and
          // leaving one beside an absolute field is what would let a reader add
          // the rack's offsets to a server's a second time.
          if (!Number.isFinite(Number(srv.inletOffset))) {
              srv.inletOffset = (Number(model.inletOffset) || 0) + (Number(srv.extraInletOffset) || 0);
          }
          if (!Number.isFinite(Number(srv.outletOffset))) {
              srv.outletOffset = (Number(model.outletOffset) || 0) + (Number(srv.extraOutletOffset) || 0);
          }
          delete srv.extraInletOffset;
          delete srv.extraOutletOffset;
          const _clampedDepth = _clampServerDepthOffsets(srv.inletOffset, srv.outletOffset, _depthM);
          srv.inletOffset = _clampedDepth.inletOffset;
          srv.outletOffset = _clampedDepth.outletOffset;
          // Per-server opacity (spec §3.1): meta.display.opacity is the ONLY
          // storage; the rack sidebar's server slider is a batch writer over it.
          if (!srv.meta.display || typeof srv.meta.display !== 'object') srv.meta.display = {};
          srv.meta.display.opacity = Number.isFinite(Number(srv.meta.display.opacity))
              ? Math.min(1, Math.max(0, Number(srv.meta.display.opacity)))
              : _rackOpacity;
          // Outlet patch validation (faces.outlet.patches[0])
          if (srv.faces && srv.faces.outlet && srv.faces.outlet.patches && srv.faces.outlet.patches.length > 0) {
              var p = srv.faces.outlet.patches[0];
              if (p.type === 'circle') {
                  p.cx = Math.min(1, Math.max(0, Number(p.cx) || 0.5));
                  p.cy = Math.min(1, Math.max(0, Number(p.cy) || 0.5));
                  p.r  = Math.min(0.5, Math.max(0.05, Number(p.r) || 0.3));
              } else {
                  p.x = Math.min(1, Math.max(0, Number(p.x) || 0));
                  p.y = Math.min(1, Math.max(0, Number(p.y) || 0));
                  p.w = Math.min(1 - p.x, Math.max(0.05, Number(p.w) || 0.6));
                  p.h = Math.min(1 - p.y, Math.max(0.05, Number(p.h) || 0.8));
              }
          }
          _byUid[uid] = srv;
      });
      // Pass 2 — placement. Same walk findServerDepthConflicts runs, so a rack
      // the predicate calls clean is a rack this pass keeps whole.
      const _placement = _resolveServerPlacement(
          geom.slot,
          _serverPlacementRecords(model, Object.keys(_byUid).map(uid => ({ key: uid, srv: _byUid[uid] })))
      );
      const _normalizedServers = {};
      _placement.kept.forEach(uid => { if (_byUid[uid]) _normalizedServers[uid] = _byUid[uid]; });
      cons0.servers = _normalizedServers;
      // Diagnostics for documents edited outside the UI (spec §4.2): rebuilt on
      // every sync, empty when nothing was dropped, and never read by geometry.
      cons0.serverConflicts = _placement.conflicts;

      // deletedFaces lives at model.deletedFaces (top-level V4); unitCount is geometry.slot.
      // Inlet (front) defaults visible (deleted=false), outlet (back) defaults blanked (deleted=true).
      if (!model.deletedFaces) model.deletedFaces = { front: [], back: [] };
      const _df = model.deletedFaces;
      const _unitCount = geom.slot;
      _df.front = Array.from({ length: _unitCount }, (_, index) => index < (_df.front || []).length ? !!_df.front[index] : false);
      _df.back = Array.from({ length: _unitCount }, (_, index) => index < (_df.back || []).length ? !!_df.back[index] : true);
      // Partitions: fully manual, preserve user state only.
      // Default: partition is deleted (hidden). User must manually enable via "Add partition" button.
      const cons = model.constructions || (model.constructions = {});
      const prevPartitions = Array.isArray(cons.partitions) ? cons.partitions : [];
      cons.partitions = Array.from({ length: Math.max(0, geom.slot - 1) }, (_, index) => {
          const previous = prevPartitions.find(item => item.index === index);
          const deleted = previous ? !!previous.deleted : true;
          return {
              index,
              name: previous?.name || `partition_${index + 1}`,
              deleted: deleted
          };
      });
  }

  function getDefaultModelTransform() {
      // Classic scripts load the kernel first; standalone CJS callers load the
      // same file here, rather than keeping a second set of default arithmetic.
      const kernel = typeof window !== 'undefined' && window.modelPosePorts
          || typeof globalThis !== 'undefined' && globalThis.modelPosePorts
          || typeof require === 'function' && require('./model_pose_ports.js');
      if (!kernel) throw new Error('rack_state.js needs js/model_pose_ports.js loaded first');
      return kernel.getDefaultModelTransform();
  }

  function getDefaultModelMoveTool() {
      return {
          enabled: false,
          axes: { x: true, y: true, z: true }
      };
  }

  function getDefaultCfd(kind) {
      switch (kind) {
          case 'rack': return { role: 'rack', servers: [], leakage: { enabled: false, sigma: 0.05 } };
          case 'acu': return { role: 'acu', patchPhysics: {} };
          case 'cdu': return { role: 'cdu' };
          case 'baffle': return {
              role: 'baffle',
              baffleType: 'wall',
              perforated: { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 },
              patchPhysics: {}
          };
          case 'partitionWall': return { role: 'partitionWall', patchPhysics: {} };
          case 'floor': return { role: 'floor', roomBoundaryType: 'none', openings: [], defaultBC: { type: 'wallBaffle' } };
          case 'wall': return { role: 'wall' };
          default: return { role: 'wall' };
      }
  }

  function createRackModel(name = 'Default_Rack') {
      const seed = {
          id: _nextModelId(),
          kind: 'rack',
          name,
          enabled: true,
          showAnchorMarker: true,
          opacity: 1,
          anchor: { x: 0, y: 0, z: 0 },
          // EIA-310 / IEC 60297 42U enterprise cabinet:
          //   W 600 mm (19" mounting strips 482.6 mm → slotWidthRatio ≈ 0.8)
          //   D 1200 mm (modern enterprise default; accommodates 750-900 mm
          //     servers plus rear cable-management plenum)
          //   H 42 × 1U = 42 × 44.45 mm = 1866.9 mm (usable); syncRackModelState
          //     adds safeMarginTop + safeMarginBottom to produce the external height.
          base: { W: 0.6, D: 1.2, H: 42 * RACK_DEFAULT_UNIT_HEIGHT },
          transform: getDefaultModelTransform(),
          moveTool: getDefaultModelMoveTool(),
          faces: createFacesState({ front: 'inlet', back: 'outlet' }),
          solidRegions: [],
          customSTLBase64: null,
          customOffset: { x: 0, y: 0, z: 0 },
          cfd: getDefaultCfd('rack'),
          rack: getDefaultRackState()
      };
      // syncRackModelState is V4-native (reads model.geometry.size.* + the rack
      // proxy), so finalize the v3 seed to a native V4 model FIRST, then sync runs
      // against the V4 geometry/cooling/constructions paths through the legacy aliases.
      const model = _stateV4().finalizeNewModelV4(seed);
      syncRackModelState(model);
      return model;
  }

  return {
    configureIds,
    FACE_CONFIGS,
    // Rack face roles — display names only (see above FACE_CONFIGS).
    RACK_SIDE_FLOW_ROLE,
    rackFaceRole,
    rackFaceLabelKey,
    rackFaceColorLabelKey,
    rackFaceLabel,
    RACK_DEFAULT_UNIT_HEIGHT,
    RACK_EQUIPMENT_PRESETS,
    _RACK_SERVER_MAPPED,
    createFacesState,
    getDefaultRackState,
    getDefaultEquipmentFaces,
    _migrateOutletPatchToFaces,
    getRackEquipmentPreset,
    cloneRackState,
    _rackV3View,
    _rackServerView,
    syncRackModelState,
    getDefaultModelTransform,
    getDefaultModelMoveTool,
    getDefaultCfd,
    createRackModel,
    // Stacked servers (spec 2026-09-17 §3-§4). The three public names are also
    // hung on window directly by the UMD wrapper above; the underscore helpers
    // are exported for the sibling shared modules (geometry_pure.js reads
    // _serverDepthInterval so there is ONE definition of a server's depth) and
    // for tests.
    SERVER_DEPTH_EPSILON_M,
    SERVER_DEPTH_MAX_FRACTION,
    findServerDepthConflicts,
    defaultServerDepthOffsets,
    setServerDepthAbsolute,
    _rackDepthM,
    _clampServerDepthOffsets,
    _serverDepthInterval,
    _serverSlotSpan,
    _serverUid,
    _serversDictOf,
    _bySlotThenInlet,
    _resolveServerPlacement,
    _serverPlacementRecords,
  };
});
