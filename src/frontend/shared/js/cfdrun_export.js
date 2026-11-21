/*
 * cfdrun_export.js — V2 .cfdrun translators (live V4 state -> manifest fragments)
 *
 * Purpose: read a V4 in-memory `state` (the migrate+normalize of a .cfdproj) and
 * produce the V2 manifest fragments that cfdrun_schema.js defines structurally.
 * This file is the translator LAYER; the manifest STRUCTURE (skeleton builders,
 * enums, validateManifest) lives in cfdrun_schema.js.
 *
 * AD1 contract (Execution Architecture Decisions, 2026-06-14):
 *   - Translators are PURE, node-runnable functions of `(state, stlMap, ...)`.
 *   - NO Three.js / DOM / window.* reads inside translators.
 *   - `state` is the V4 in-memory state; `stlMap` is the AD2 rich-solid map.
 *   - Dual-export idiom identical to cfdrun_schema.js (window.cfdrunExport +
 *     module.exports). May require('./cfdrun_schema.js') for shared helpers.
 *
 * This sub-task (P3 T2a) implements the FIRST three translators:
 *   - assembleInputs(state)            -> RunInputs        (schema §3)
 *   - assembleGeometryExport(state,map)-> GeometryExport   (schema §4 + AD2/AD3)
 *   - buildSourceProject(state, when)  -> Promise<SourceProject> (schema §2 + AD4)
 * Binding / source-term translators (acus/racks/openings/monitors) are LATER
 * sub-tasks (T2b–T2f) and are deliberately NOT built here.
 *
 * Authoritative field spec: docs/reference/cfdrun-v2-schema.md.
 */
(function () {
  'use strict';

  // cfdrun_schema.js dependency (node side). In the browser the schema is on
  // window.cfdrunSchema; in node we require it. Only used here for ROLES default
  // and to mirror the dual-export idiom — the heavy structure builders live there.
  var schema = null;
  if (typeof require === 'function') {
    try { schema = require('./cfdrun_schema.js'); } catch (e) { schema = null; }
  }
  if (!schema && typeof window !== 'undefined' && window.cfdrunSchema) {
    schema = window.cfdrunSchema;
  }

  // ----------------------------------------------------------------------------
  // sanitizeName — MUST match backend bridge_bc._sanitize:
  //   re.sub(r'[^A-Za-z0-9_]', '_', name)
  // Every character not in [A-Za-z0-9_] becomes an underscore.
  // ----------------------------------------------------------------------------
  function sanitizeName(name) {
    return String(name == null ? '' : name).replace(/[^A-Za-z0-9_]/g, '_');
  }

  // num(v) — return v only when it is a finite number, else undefined (so the
  // caller can omit the key rather than write `field: undefined`).
  function num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : undefined;
  }

  // assign(obj, key, value) — set obj[key] only when value !== undefined.
  function assign(obj, key, value) {
    if (value !== undefined) obj[key] = value;
  }

  // ----------------------------------------------------------------------------
  // assembleInputs(state) -> RunInputs  (schema §3)
  //
  // A run-condition snapshot. Only emits keys that exist; never fabricates
  // values; omits undefined fields. Field paths in V4 state were verified
  // against migrated Base_leakage / Liquid / Shinaide / STDCT / CMI cases:
  //   server.cooling.power           -> inputPowerW
  //   server.cooling.serverBCMode    -> bcMode
  //   server.cooling.volumeFlowRate  -> airVolumeFlowRateM3s
  //   server.cooling.temperatureRise -> temperatureRiseC
  //   server.meta.enabled            -> serverSwitch
  //   acu supply patch physics       -> supply temp / flow / setpoint / coolant
  //   rack.constructions.rdhx        -> racks[*].rdhx
  //   state.runDefaults.liquid       -> liquid {enabled, inheritResults}
  //   state.instrumentation.measurements -> labels.sensorMeasurements
  // ----------------------------------------------------------------------------
  // assertUniqueServerUids — server uids must be globally unique across racks.
  // The cfdrun bySource join key ("<serverUid>|<faceId>|<patchId>") and the flat
  // inputs.servers dict are BOTH uid-keyed, so a uid reused in two racks silently
  // collapses: one rack's fan-server patches lose their binding, the runner can't
  // find them in the BC registry, and field generation hits the generic-T
  // fallthrough AFTER meshing (a cryptic late failure). Fail fast at export with
  // an actionable message instead. (paintToDC1 had e_3 reused across 24 racks.)
  function assertUniqueServerUids(state) {
    var cons = (state && state.constructions) || {};
    var racks = cons.racks || {};
    var firstRack = {};
    Object.keys(racks).forEach(function (rackUid) {
      var rack = racks[rackUid] || {};
      var servers = (rack.constructions || {}).servers || {};
      Object.keys(servers).forEach(function (serverUid) {
        if (Object.prototype.hasOwnProperty.call(firstRack, serverUid)) {
          throw new Error(
            'duplicate server uid "' + serverUid + '" in racks "' +
            firstRack[serverUid] + '" and "' + rackUid + '" — server uids must be ' +
            'globally unique (the cfdrun bySource join and inputs.servers are ' +
            'uid-keyed). Re-save the project in the editor to reassign uids.');
        }
        firstRack[serverUid] = rackUid;
      });
    });
  }

  function assembleInputs(state) {
    var st = state || {};
    assertUniqueServerUids(st);
    var cons = st.constructions || {};

    var inputs = {
      racks: {},
      acus: {},
      servers: {},
      cdus: {},
      partitionWalls: {},
      pipeSegments: {},
      liquid: assembleLiquid(st),
      labels: { sensorMeasurements: assembleSensorMeasurements(st) },
    };

    // ---- racks (rdhx) + servers (nested inside each rack) ----
    var racks = cons.racks || {};
    Object.keys(racks).forEach(function (rackUid) {
      var rack = racks[rackUid] || {};
      var rackCons = rack.constructions || {};

      // rdhx: only racks that HAVE an enabled/defined rdhx contribute a rack entry
      var rdhx = rackCons.rdhx;
      if (rdhx && typeof rdhx === 'object') {
        var rdhxOut = {};
        assign(rdhxOut, 'enabled', typeof rdhx.enabled === 'boolean' ? rdhx.enabled : undefined);
        assign(rdhxOut, 'waterFlowRateLpm', num(rdhx.waterFlowRate));
        assign(rdhxOut, 'waterInletTempC', num(rdhx.waterInletTemp));
        assign(rdhxOut, 'fanSpeedRatio', num(rdhx.fanSpeedRatio));
        if (Object.keys(rdhxOut).length > 0) {
          inputs.racks[rackUid] = { rdhx: rdhxOut };
        }
      }

      // servers live inside the rack: rack.constructions.servers, keyed by uid
      var servers = rackCons.servers || {};
      Object.keys(servers).forEach(function (serverUid) {
        var entry = assembleServerInput(servers[serverUid]);
        if (entry) inputs.servers[serverUid] = entry;
      });
    });

    // ---- acus ----
    var acus = cons.acus || {};
    Object.keys(acus).forEach(function (acuUid) {
      var entry = assembleAcuInput(acus[acuUid]);
      if (entry) inputs.acus[acuUid] = entry;
    });

    // ---- cdus ----
    var cdus = cons.cdus || {};
    Object.keys(cdus).forEach(function (cduUid) {
      var entry = assembleCduInput(cdus[cduUid]);
      if (entry && Object.keys(entry).length > 0) inputs.cdus[cduUid] = entry;
    });

    return inputs;
  }

  function assembleServerInput(server) {
    if (!server || typeof server !== 'object') return null;
    var cooling = server.cooling || {};
    var meta = server.meta || {};
    var out = {};
    assign(out, 'serverSwitch', typeof meta.enabled === 'boolean' ? meta.enabled : undefined);
    assign(out, 'inputPowerW', num(cooling.power));
    assign(out, 'inputPowerPercentage', num(cooling.inputPowerPercentage));
    assign(out, 'airVolumeFlowRateM3s', num(cooling.volumeFlowRate));
    assign(out, 'temperatureRiseC', num(cooling.temperatureRise));
    assign(out, 'liquidPercentage', num(cooling.liquidPercentage));
    assign(out, 'liquidMassFlowRateKgs', num(cooling.liquidMassFlowRateKgs));
    assign(out, 'bcMode', typeof cooling.serverBCMode === 'string' ? cooling.serverBCMode : undefined);
    return out;
  }

  // Supply-patch physics carry the run-condition setpoints; coolingMode comes
  // from the ACU's performance mode (model.acuPerformanceMode, default fixedTemp).
  function assembleAcuInput(acu) {
    if (!acu || typeof acu !== 'object') return null;
    var meta = acu.meta || {};
    var out = {};
    assign(out, 'acuSwitch', typeof meta.enabled === 'boolean' ? meta.enabled : undefined);

    var supply = firstAcuSupplyPhysics(acu);
    if (supply) {
      assign(out, 'supplyAirTemperatureC', num(supply.temperature));
      assign(out, 'supplyAirVolumeFlowRateM3s', num(supply.volumeFlowRate));
      assign(out, 'setPointTemperatureC', num(supply.setPointTemperature));
      assign(out, 'coolantTemperatureC', num(supply.coolantTemperature));
    }

    var mode = (typeof acu.acuPerformanceMode === 'string' && acu.acuPerformanceMode)
      ? acu.acuPerformanceMode
      : 'fixedTemp';
    out.coolingMode = mode;

    return out;
  }

  // Returns the physics block of the first supply patch found across all faces,
  // or null. ACU faces are a uid-keyed dict; each face has a `patches` array.
  function firstAcuSupplyPhysics(acu) {
    var geom = acu.geometry || {};
    var faces = geom.faces;
    if (!faces || typeof faces !== 'object') return null;
    var faceList = Array.isArray(faces) ? faces : Object.keys(faces).map(function (k) { return faces[k]; });
    for (var i = 0; i < faceList.length; i++) {
      var f = faceList[i];
      var patches = (f && f.patches) || [];
      for (var j = 0; j < patches.length; j++) {
        var p = patches[j];
        if (p && p.physics && p.physics.role === 'supply') return p.physics;
      }
    }
    return null;
  }

  function assembleCduInput(cdu) {
    if (!cdu || typeof cdu !== 'object') return null;
    var cooling = cdu.cooling || {};
    var out = {};
    assign(out, 'coolingWaterSupplyTemperatureSpC', num(cooling.coolingWaterSupplyTemperatureSp));
    assign(out, 'chilledWaterSupplyTemperatureC', num(cooling.chilledWaterSupplyTemperature));
    assign(out, 'chilledWaterMassFlowRateKgs', num(cooling.chilledWaterMassFlowRate));
    // supplyTemperatureC / supplyPressureKpa are the V4 CDU cooling homes; expose
    // them under the schema's pressure key when present.
    assign(out, 'supplyPressureKpa', num(cooling.supplyPressureKpa));
    assign(out, 'valveOpening', num(cooling.valveOpening));
    return out;
  }

  // liquid: from state.runDefaults.liquid (Plan-2 V4 home).
  function assembleLiquid(state) {
    var liquid = (state.runDefaults && state.runDefaults.liquid) || {};
    return {
      enabled: liquid.enabled === true,
      inheritResults: liquid.inheritResults === true,
    };
  }

  // labels.sensorMeasurements: from state.instrumentation.measurements.
  // Each entry may carry temperatureC and/or relativeHumidityPct.
  function assembleSensorMeasurements(state) {
    var measurements = (state.instrumentation && state.instrumentation.measurements) || {};
    var out = {};
    Object.keys(measurements).forEach(function (sensorId) {
      var m = measurements[sensorId] || {};
      var entry = {};
      assign(entry, 'temperatureC', num(m.temperatureC));
      assign(entry, 'relativeHumidityPct', num(m.relativeHumidityPct));
      out[sensorId] = entry;
    });
    return out;
  }

  // ----------------------------------------------------------------------------
  // assembleGeometryExport(state, stlMap) -> GeometryExport  (schema §4 + AD2/AD3)
  //
  // stlMap (AD2) is a rich solid map keyed by solidName:
  //   { solidName, stlFileId, objectPath, surfaceUid, kind, path,
  //     source:{ modelUid, faceId?, patchId? }, areaM2?, role?,
  //     shape?, width?, height?, radius?, depth? }
  // Tolerates null/{} -> skeleton with empty solids/stlFiles.
  // ----------------------------------------------------------------------------
  function assembleGeometryExport(state, stlMap) {
    var ge = {
      coordinateSystem: 'Y-up',
      unit: 'm',
      stlFiles: {},
      solids: {},
    };
    if (!stlMap || typeof stlMap !== 'object') return ge;

    var solidNames = Object.keys(stlMap);
    if (solidNames.length === 0) return ge;

    var usedSolidIds = {};

    solidNames.forEach(function (solidName) {
      var entry = stlMap[solidName] || {};
      var sName = entry.solidName != null ? entry.solidName : solidName;
      var patchName = sanitizeName(sName);

      // Stable uid for the solid binding: sol_<sanitizedSolidName> (de-duped).
      var solidId = 'sol_' + patchName;
      if (usedSolidIds[solidId]) {
        var n = usedSolidIds[solidId] + 1;
        usedSolidIds[solidId] = n;
        solidId = solidId + '_' + n;
      } else {
        usedSolidIds[solidId] = 1;
      }

      var role = (typeof entry.role === 'string' && entry.role) ? entry.role : 'wall';

      var binding = {
        solidName: sName,
        patchName: patchName,
        stlFileId: entry.stlFileId != null ? entry.stlFileId : null,
        objectPath: entry.objectPath != null ? entry.objectPath : null,
        surfaceUid: entry.surfaceUid != null ? entry.surfaceUid : null,
        role: role,
        areaM2: computeAreaM2(state, entry),
      };
      var perimeter = num(entry.perimeterM);
      if (perimeter !== undefined) binding.perimeterM = perimeter;

      ge.solids[solidId] = binding;

      // Materialize the StlFile for this entry's stlFileId (once per distinct id).
      var fileId = entry.stlFileId;
      if (fileId != null && !(fileId in ge.stlFiles)) {
        ge.stlFiles[fileId] = {
          path: entry.path != null ? entry.path : null,
          objectPath: entry.objectPath != null ? entry.objectPath : null,
          kind: entry.kind != null ? entry.kind : null,
          // STL content hashing is a browser/T3 concern; leave null here.
          contentHash: entry.contentHash != null ? entry.contentHash : null,
        };
      }
    });

    return ge;
  }

  // computeAreaM2 (AD3) — EXACT area from `state` geometry for simple shapes:
  //   patch circle  -> π·r²
  //   patch/face rect -> w·h
  //   floor/baffle rectangle -> w·d
  // For wall-remainder / polygon-face / custom-STL solids, fall back to
  // stlMap[entry].areaM2 when present, else null. NEVER fabricated.
  function computeAreaM2(state, entry) {
    var shape = entry.shape;

    if (shape === 'circle') {
      var r = num(entry.radius);
      if (r !== undefined) return Math.PI * r * r;
    }
    if (shape === 'rect' || shape === 'rectangle') {
      var w = num(entry.width);
      var h = num(entry.height);
      if (w !== undefined && h !== undefined) return w * h;
      // floor/baffle rectangle expressed as width × depth
      var d = num(entry.depth);
      if (w !== undefined && d !== undefined) return w * d;
    }

    // Fallback: an area precomputed by the browser caller into the map.
    var mapped = num(entry.areaM2);
    if (mapped !== undefined) return mapped;
    return null;
  }

  // ----------------------------------------------------------------------------
  // buildSourceProject(state, exportedAt) -> Promise<SourceProject>  (schema §2 + AD4)
  //
  // contentHash = lowercase 64-char SHA-256 hex of a deterministic JSON
  // serialization of `state` (recursive key-sort -> the same state always hashes
  // identically). exportedAt is INJECTED (deterministic/testable) — callers pass
  // new Date().toISOString(); defaults to null when not given.
  // ----------------------------------------------------------------------------
  function buildSourceProject(state, exportedAt) {
    var st = state || {};
    var contentHash;
    // Same canonical (disk-form) content as projectContentHashSync.
    return sha256Hex(stableStringify(canonicalProjectForHash(st))).then(function (hash) {
      contentHash = hash;
      var name = st.name;
      if (name == null && st.meta) name = st.meta.name || st.meta.projectName;
      return {
        uid: st.uid != null ? st.uid : null,
        schemaVersion: st.schemaVersion != null ? st.schemaVersion : null,
        projectName: name != null ? name : null,
        filePath: (st.meta && st.meta.casePath != null) ? st.meta.casePath : null,
        contentHash: contentHash,
        exportedAt: exportedAt != null ? exportedAt : null,
      };
    });
  }

  // stableStringify — deterministic JSON serialization with recursively sorted
  // object keys so the same logical state always produces the same string.
  // Arrays keep their order (order is meaningful). undefined/functions are
  // dropped by JSON semantics; this mirrors JSON.stringify value handling.
  function stableStringify(value) {
    return JSON.stringify(sortKeysDeep(value));
  }

  function sortKeysDeep(value) {
    if (Array.isArray(value)) {
      return value.map(sortKeysDeep);
    }
    if (value && typeof value === 'object') {
      // A plain {} silently drops JSON's own "__proto__" key through its
      // inherited setter, producing a hash that no backend can reproduce.
      var out = Object.create(null);
      Object.keys(value).sort().forEach(function (k) {
        out[k] = sortKeysDeep(value[k]);
      });
      return out;
    }
    return value;
  }

  // sha256Hex — resolve WebCrypto (browser SubtleCrypto or node webcrypto, AD4).
  function getSubtle() {
    if (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.subtle) {
      return globalThis.crypto.subtle;
    }
    if (typeof require === 'function') {
      try { return require('node:crypto').webcrypto.subtle; } catch (e) { /* fall through */ }
    }
    return null;
  }

  function sha256Hex(str) {
    var bytes = utf8Bytes(str);
    var subtle = getSubtle();
    if (subtle) {
      return Promise.resolve(subtle.digest('SHA-256', bytes)).then(function (buf) {
        var view = new Uint8Array(buf);
        var hex = '';
        for (var i = 0; i < view.length; i++) {
          hex += (view[i] + 0x100).toString(16).slice(1);
        }
        return hex;
      });
    }
    // No WebCrypto: SubtleCrypto is restricted to secure contexts (https / localhost),
    // so it is absent when the page is served over a LAN IP via plain HTTP. Fall back to
    // a pure-JS SHA-256 over the SAME bytes — the hash is identical and export still works.
    return Promise.resolve(sha256HexJS(bytes));
  }

  // Synchronous hash for the v3 liquid compiler, which has a synchronous
  // manifest API. It uses the exact stable JSON and pure-JS SHA-256 fallback
  // already used by the asynchronous air sourceProject exporter.
  function projectContentHashSync(state) {
    return sha256HexJS(utf8Bytes(stableStringify(canonicalProjectForHash(state || {}))));
  }

  // cfdproj v5: a live project carries its time-variable fields materialised
  // at the playhead; on disk they exist only in `transient.tracks`. The hash
  // is of the disk form, so a live project, a reopened file and a copy
  // materialised at another time all bind to the same content.
  function canonicalProjectForHash(state) {
    var tr = state && state.transient;
    if (!tr || !Array.isArray(tr.tracks) || !tr.tracks.length) return state;
    var T = (typeof window !== 'undefined' && window.cfdprojTransient) ||
      (typeof require === 'function' ? require('./cfdproj_transient.js') : null);
    if (!T) throw new Error('cfdrun_export: cfdproj_transient.js is required to hash a v5 project');
    return T.stripTrackedFields(JSON.parse(JSON.stringify(state)));
  }

  function sha256TextSync(value) {
    return sha256HexJS(utf8Bytes(value));
  }

  // sha256HexJS — FIPS 180-4 SHA-256 over a Uint8Array, returning lowercase hex.
  // Pure-JS fallback for insecure contexts where WebCrypto SubtleCrypto is unavailable.
  // Verified byte-for-byte against WebCrypto / Node crypto across multi-block + Unicode inputs.
  function sha256HexJS(bytes) {
    function rotr(n, x) { return (x >>> n) | (x << (32 - n)); }
    var K = [
      0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    ];
    var H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    var l = bytes.length;
    // pad: message + 0x80 + zeros + 64-bit big-endian bit length, to a multiple of 64 bytes
    var padded = new Uint8Array(Math.ceil((l + 9) / 64) * 64);
    padded.set(bytes);
    padded[l] = 0x80;
    var bitLenHi = Math.floor(l / 0x20000000); // high 32 bits of (l * 8)
    var bitLenLo = (l * 8) >>> 0;
    var p = padded.length;
    padded[p-8]=(bitLenHi>>>24)&0xff; padded[p-7]=(bitLenHi>>>16)&0xff; padded[p-6]=(bitLenHi>>>8)&0xff; padded[p-5]=bitLenHi&0xff;
    padded[p-4]=(bitLenLo>>>24)&0xff; padded[p-3]=(bitLenLo>>>16)&0xff; padded[p-2]=(bitLenLo>>>8)&0xff; padded[p-1]=bitLenLo&0xff;
    var w = new Array(64);
    for (var i = 0; i < padded.length; i += 64) {
      for (var t = 0; t < 16; t++) {
        w[t] = ((padded[i+t*4]<<24) | (padded[i+t*4+1]<<16) | (padded[i+t*4+2]<<8) | padded[i+t*4+3]) | 0;
      }
      for (t = 16; t < 64; t++) {
        var s0 = rotr(7,w[t-15]) ^ rotr(18,w[t-15]) ^ (w[t-15] >>> 3);
        var s1 = rotr(17,w[t-2]) ^ rotr(19,w[t-2]) ^ (w[t-2] >>> 10);
        w[t] = (w[t-16] + s0 + w[t-7] + s1) | 0;
      }
      var a=H[0],b=H[1],c=H[2],d=H[3],e=H[4],f=H[5],g=H[6],h=H[7];
      for (t = 0; t < 64; t++) {
        var S1 = rotr(6,e) ^ rotr(11,e) ^ rotr(25,e);
        var ch = (e & f) ^ (~e & g);
        var temp1 = (h + S1 + ch + K[t] + w[t]) | 0;
        var S0 = rotr(2,a) ^ rotr(13,a) ^ rotr(22,a);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var temp2 = (S0 + maj) | 0;
        h=g; g=f; f=e; e=(d+temp1)|0; d=c; c=b; b=a; a=(temp1+temp2)|0;
      }
      H[0]=(H[0]+a)|0; H[1]=(H[1]+b)|0; H[2]=(H[2]+c)|0; H[3]=(H[3]+d)|0;
      H[4]=(H[4]+e)|0; H[5]=(H[5]+f)|0; H[6]=(H[6]+g)|0; H[7]=(H[7]+h)|0;
    }
    var hex = '';
    for (i = 0; i < 8; i++) hex += ((H[i] >>> 0) + 0x100000000).toString(16).slice(1);
    return hex;
  }

  // utf8Bytes — TextEncoder is available in modern browsers and node >= 11.
  function utf8Bytes(str) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(str);
    // Fallback (very old environments): manual UTF-8 encode.
    var utf8 = unescape(encodeURIComponent(str));
    var arr = new Uint8Array(utf8.length);
    for (var i = 0; i < utf8.length; i++) arr[i] = utf8.charCodeAt(i);
    return arr;
  }

  // ----------------------------------------------------------------------------
  // translateAcus(state, ctx) -> { boundaryBindings: {...} }  (P3 T2b)
  //
  // Emits acu_supply / acu_return BoundaryBinding entries for every ACU in
  // state.constructions.acus.  The device→solid join is AD7: the caller
  // supplies ctx.bySource keyed "<acuUid>|<faceId>|<patchId>" pointing at the
  // exported solid.  A patch with no bySource hit is SILENTLY SKIPPED (never
  // fabricate a patchName).
  //
  // Parameters follow AD8 camelCase vocabulary:
  //   acu_supply: temperatureC (default 15), flowRateM3s (default 4),
  //               coolingCapacityW (kW*1000, omit if absent),
  //               setPointTempC, coolantTempC,
  //               minSupplyTempC (= coolantTempC+6.3 if absent and coolant set),
  //               returnPatchName (first return patch, omit if none exported),
  //               coolingModelMode, ratedReturnTempC, coolingPerformanceTable,
  //               timeVarying (always emitted, default {})
  //   acu_return:  flowRateM3s (default 4), timeVarying (default {})
  //
  // Gold standard: _build_bridge_bc stlgen_runner.py:3151-3191.
  // ----------------------------------------------------------------------------
  function translateAcus(state, ctx) {
    var boundaryBindings = {};
    var st = state || {};
    var cons = st.constructions || {};
    var acus = cons.acus || {};
    var bySource = (ctx && ctx.bySource) || {};

    Object.keys(acus).forEach(function (acuUid) {
      var acu = acus[acuUid] || {};
      var geom = acu.geometry || {};
      var supplyFaces = geom.supplyFaces || [];
      var returnFaces = geom.returnFaces || [];

      // ------------------------------------------------------------------
      // Resolve the return patch name FIRST (needed by each supply binding).
      // Use the FIRST return face whose bySource hit exists.
      // ------------------------------------------------------------------
      var returnPatchName;
      for (var ri = 0; ri < returnFaces.length; ri++) {
        var rf0 = returnFaces[ri] || {};
        var rKey0 = acuUid + '|' + rf0.faceId + '|' + rf0.patchId;
        var rSol0 = bySource[rKey0];
        if (rSol0 && rSol0.patchName) {
          returnPatchName = rSol0.patchName;
          break;
        }
      }

      // ------------------------------------------------------------------
      // Supply bindings
      // ------------------------------------------------------------------
      supplyFaces.forEach(function (sf) {
        var faceId = sf.faceId;
        var patchId = sf.patchId;
        var sKey = acuUid + '|' + faceId + '|' + patchId;
        var sol = bySource[sKey];
        if (!sol) return; // AD7: no hit -> skip

        // Retrieve the patch physics from the face definition.
        var faceObj = geom.faces && geom.faces[faceId];
        var patches = (faceObj && faceObj.patches) || [];
        var phys = {};
        for (var pi = 0; pi < patches.length; pi++) {
          if (patches[pi] && patches[pi].id === patchId) {
            phys = patches[pi].physics || {};
            break;
          }
        }

        // Build the parameters object (AD8 camelCase; omit undefined/null).
        var params = {};

        // temperatureC and flowRateM3s always emitted with defaults.
        params.temperatureC = (typeof phys.temperature === 'number' && isFinite(phys.temperature))
          ? phys.temperature : 15;
        params.flowRateM3s = (typeof phys.volumeFlowRate === 'number' && isFinite(phys.volumeFlowRate))
          ? phys.volumeFlowRate : 4;

        // coolingCapacityW: kW * 1000
        var capKw = num(phys.coolingCapacity);
        if (capKw !== undefined) assign(params, 'coolingCapacityW', capKw * 1000);

        // setPointTempC
        assign(params, 'setPointTempC', num(phys.setPointTemperature));

        // coolantTempC
        var coolantC = num(phys.coolantTemperature);
        assign(params, 'coolantTempC', coolantC);

        // minSupplyTempC: use explicit value, else derive coolantTempC + 6.3 if coolant present
        var minSup = num(phys.minSupplyTemperature);
        if (minSup === undefined && coolantC !== undefined) {
          minSup = coolantC + 6.3;
        }
        assign(params, 'minSupplyTempC', minSup);

        // returnPatchName (from the resolved return patch, may be absent)
        if (returnPatchName !== undefined) {
          params.returnPatchName = returnPatchName;
        }

        // A louvred outlet on a wall-mounted unit blows along its surface
        // normal tilted by supplyAngleDeg: the world direction and the
        // patch area go with the binding so the runner can write a fixed
        // velocity vector instead of the face-normal flow rate.
        var pure = (typeof window !== 'undefined') ? window.geometryPure : null;
        var patchRec = null;
        for (var pj = 0; pj < patches.length; pj++) { if (patches[pj] && patches[pj].id === patchId) { patchRec = patches[pj]; break; } }
        if (pure && patchRec && typeof pure.isWallMountedAcu === 'function' && pure.isWallMountedAcu(acu) && faceId === 'front') {
          var dirW = pure.wallAcuSupplyDirectionWorld(acu, patchRec);
          if (dirW) {
            params.directionWorld = [dirW.x, dirW.y, dirW.z];
            var pw = Number(patchRec.w) || 0, ph = Number(patchRec.h) || 0;
            if (patchRec.type === 'circle') { pw = ph = 2 * (Number(patchRec.r) || 0); params.patchAreaM2 = Math.PI * Math.pow(pw / 2, 2); }
            else params.patchAreaM2 = pw * ph;
            if (phys.supplyAngleDeg != null) params.supplyAngleDeg = Number(phys.supplyAngleDeg) || 0;
          }
        }

        // coolingModel fields
        var cm = (phys.coolingModel && typeof phys.coolingModel === 'object') ? phys.coolingModel : {};
        assign(params, 'coolingModelMode', cm.mode != null ? cm.mode : undefined);
        assign(params, 'ratedReturnTempC', num(cm.ratedReturnTemp));
        if (cm.performanceTable !== undefined && cm.performanceTable !== null) {
          params.coolingPerformanceTable = cm.performanceTable;
        }

        // timeVarying always emitted, defaults to {}
        params.timeVarying = (phys.timeVarying && typeof phys.timeVarying === 'object')
          ? phys.timeVarying : {};

        var bbKey = 'bb_' + sol.patchName;
        boundaryBindings[bbKey] = {
          patchName: sol.patchName,
          solidName: sol.solidName,
          objectPath: 'constructions.acus.' + acuUid,
          surfaceUid: sol.surfaceUid != null ? sol.surfaceUid : (faceId + '_' + patchId),
          role: 'acu_supply',
          bcType: 'acu_supply',
          parameters: params,
        };
      });

      emitReturnBindings(acuUid, geom, 'constructions.acus.' + acuUid, returnFaces);
    });

    // Floor-hosted return grilles (a ceiling return): the floor carries
    // `geometry.returnFaces` exactly as an ACU does, and the stlMap entry was
    // put with the same source triple, so the very same binding applies —
    // only the objectPath names the floor.
    var floors = cons.floors || {};
    Object.keys(floors).forEach(function (floorUid) {
      var floor = floors[floorUid] || {};
      var fgeom = floor.geometry || {};
      var frf = fgeom.returnFaces || [];
      if (!frf.length) return;
      emitReturnBindings(floorUid, fgeom, 'constructions.floors.' + floorUid, frf);
    });

    // Box-hosted return grilles: a box patch whose `physics.role === 'return'`
    // is exported as a return-only ACU (cfdrun_build.js) and registered with
    // the same source triple, so it takes the very same acu_return binding.
    var boxes = cons.boxes || {};
    Object.keys(boxes).forEach(function (boxUid) {
      var box = boxes[boxUid] || {};
      var bgeom = box.geometry || {};
      var faces = bgeom.faces || {};
      var brf = [];
      Object.keys(faces).forEach(function (fid) {
        var patches = (faces[fid] && faces[fid].patches) || [];
        patches.forEach(function (p) {
          if (p && p.physics && p.physics.role === 'return') brf.push({ faceId: fid, patchId: p.id });
        });
      });
      if (!brf.length) return;
      emitReturnBindings(boxUid, bgeom, 'constructions.boxes.' + boxUid, brf);
    });

    function emitReturnBindings(ownerUid, geom, objectPath, returnFaces) {
      returnFaces.forEach(function (rf) {
        var faceId = rf.faceId;
        var patchId = rf.patchId;
        var rKey = ownerUid + '|' + faceId + '|' + patchId;
        var sol = bySource[rKey];
        if (!sol) return; // AD7: no hit -> skip

        // Retrieve the patch physics from the face definition.
        var faceObj = geom.faces && geom.faces[faceId];
        var patches = (faceObj && faceObj.patches) || [];
        var phys = {};
        for (var pi = 0; pi < patches.length; pi++) {
          if (patches[pi] && patches[pi].id === patchId) {
            phys = patches[pi].physics || {};
            break;
          }
        }

        var retParams = {
          flowRateM3s: (typeof phys.volumeFlowRate === 'number' && isFinite(phys.volumeFlowRate))
            ? phys.volumeFlowRate : 4,
          timeVarying: (phys.timeVarying && typeof phys.timeVarying === 'object')
            ? phys.timeVarying : {},
        };

        var bbKey = 'bb_' + sol.patchName;
        boundaryBindings[bbKey] = {
          patchName: sol.patchName,
          solidName: sol.solidName,
          objectPath: objectPath,
          surfaceUid: sol.surfaceUid != null ? sol.surfaceUid : (faceId + '_' + patchId),
          role: 'acu_return',
          bcType: 'acu_return',
          parameters: retParams,
        };
      });
    }

    return { boundaryBindings: boundaryBindings };
  }

  // ----------------------------------------------------------------------------
  // translateRackServers(state, ctx) -> { boundaryBindings: {...} }  (P3 T2c-1)
  //
  // Emits fan_inlet / fan_outlet / nonfan_fixed_temp / nonfan_heat_flux / wall
  // BoundaryBinding entries for every air-cooled rack server, applies the
  // per-patch flow split, and overrides fan_outlet temperatures for racks whose
  // Rear-Door Heat eXchanger (RDHX) is enabled.
  //
  // Gold reference: _build_bridge_bc, stlgen_runner.py:3229-3470. The Python
  // derivation runs on the v1 `.cfdrun` export shape (server.physics, spanSlots,
  // faces[x].patches[].solid). This translator runs on the V4 in-memory state
  // (AD9 Arch A); the V4→physics mapping mirrors buildCfdrunJson's server emit
  // (STL_generate.html:32521-32597). Confirmed V4 paths (inspected on migrated
  // Base_leakage + CMI_China):
  //   racks bucket        : state.constructions.racks[rackUid]
  //   servers             : rack.constructions.servers[serverUid] (uid-keyed)
  //   type discriminator  : server.type ∈ 'fanServer'|'nonFan'|'liquidServer'
  //   physics             : server.cooling.{power, volumeFlowRate, serverBCMode,
  //                         temperatureRise, timeVarying}  (fan)
  //                         server.cooling.{powerMode, inletMode/outletMode,
  //                         inletHeatType/outletHeatType, inletTemperature/
  //                         outletTemperature, inletPower/outletPower,
  //                         fixedTemperature}  (nonFan, per-face)
  //   spanSlots           : server.geometry.slotOccupation
  //   faces               : server.faces[faceId].patches[]  (top-level on server)
  //   rack geom W         : rack.geometry.size.x
  //   slotWidthRatio      : rack.geometry.slotWidthRatio (default 0.8)
  //   unitHeight          : rack.geometry.slotHeight       (default 0.04445)
  //   ambient temp        : state.geometry.temperature     (default 22)
  //   rdhx                : rack.constructions.rdhx.{enabled, waterFlowRate,
  //                         waterInletTemp, effectiveness}
  //
  // Join (AD7): the caller supplies ctx.bySource keyed by
  //   "<serverUid>|<faceId>|<patchId>"  for each patch solid,
  //   "<serverUid>|<faceId>|__face__"    for a whole-face (un-subdivided) solid,
  //   "<serverUid>|<faceId>|__wall__"    for the wall remainder.
  // A surface with no bySource hit is silently skipped (AD9) — never fabricate.
  //
  // DEFERRED (spec A14): NO liquid-residual power override. The 4 air baselines
  // (Base_leakage/Shinaide/CMI_China/STDCT) have no liquid_results, so using the
  // full server power reproduces their registries. The inheritLiquidResults
  // residual-heat coupling stays as broken as it is today; fix tracked separately.
  // ----------------------------------------------------------------------------

  // Physics constants — MUST match backend/aircooling/utils/const.py.
  var RHO_AIR = 1.19;            // kg/m^3
  var AIR_SPECIFIC_HEAT = 1005;  // J/(kg K)

  function translateRackServers(state, ctx) {
    var boundaryBindings = {};
    var st = state || {};
    var cons = st.constructions || {};
    var racks = cons.racks || {};
    var bySource = (ctx && ctx.bySource) || {};
    var ambientTemp = (st.geometry && typeof st.geometry.temperature === 'number' && isFinite(st.geometry.temperature))
      ? st.geometry.temperature : 22.0;

    Object.keys(racks).forEach(function (rackUid) {
      var rack = racks[rackUid] || {};
      var rackGeom = rack.geometry || {};
      var rackW = num((rackGeom.size || {}).x);
      if (rackW === undefined) rackW = 0.6;
      var slotRatio = num(rackGeom.slotWidthRatio);
      if (slotRatio === undefined) slotRatio = 0.8;
      var unitH = num(rackGeom.slotHeight);
      if (unitH === undefined) unitH = 0.04445;

      var rackCons = rack.constructions || {};
      var servers = rackCons.servers || {};

      // Track each fanServer's emitted fan_outlet binding keys so the RDHX pass
      // can override their temperatureC after the per-server loop.
      var rackFanOutletKeys = [];

      Object.keys(servers).forEach(function (serverUid) {
        var srv = servers[serverUid] || {};
        var cooling = srv.cooling || {};
        var air = resolveServerAirPhysics(srv.type || 'fanServer', cooling);
        var srvType = air.type;
        if (srvType !== 'fanServer' && srvType !== 'nonFan') return;
        var span = num((srv.geometry || {}).slotOccupation);
        if (span === undefined) span = 1;
        var objectPath = 'constructions.racks.' + rackUid + '.servers.' + serverUid;

        var Q = air.volumeFlowRate;
        var P = air.power;
        var srvTV = (cooling.timeVarying && typeof cooling.timeVarying === 'object')
          ? cooling.timeVarying : {};

        var faces = srv.faces || {};

        FACE_ORDER.forEach(function (faceId) {
          var faceDef = faces[faceId];
          if (!faceDef) return;

          // ---- determine the face-level BC spec ----
          var faceBc;
          if (srvType === 'fanServer') {
            var physArea = rackW * slotRatio * span * unitH;
            if (faceId === 'inlet') {
              faceBc = {
                bcType: 'fan_inlet',
                flowRateM3s: Q,
                powerW: P,
                physicalFlowAreaM2: physArea,
                timeVarying: srvTV,
              };
            } else if (faceId === 'outlet') {
              var tOut = Q > 0 ? (25.0 + P / (RHO_AIR * AIR_SPECIFIC_HEAT * Q)) : 35.0;
              faceBc = {
                bcType: 'fan_outlet',
                flowRateM3s: Q,
                powerW: P,
                temperatureC: tOut,
                physicalFlowAreaM2: physArea,
                timeVarying: srvTV,
              };
            } else {
              faceBc = { bcType: 'wall' };
            }
          } else {
            // nonFan — per-face independent BC. The V4 state stores the user's
            // intent on cooling.{powerMode,inletMode,...}; mirror buildCfdrunJson's
            // facePhysics() derivation (STL_generate.html:32560-32571) to produce
            // the same bcType/temperature/power the gold derivation consumed.
            if (faceId === 'inlet' || faceId === 'outlet') {
              var facePhys = nonFanFacePhysics(cooling, faceId);
              if (facePhys.bcType === 'nonfan_fixed_temp') {
                var tVal = (typeof facePhys.temperature === 'number' && isFinite(facePhys.temperature))
                  ? facePhys.temperature : 35.0;
                faceBc = { bcType: 'nonfan_fixed_temp', temperatureC: tVal };
              } else if (facePhys.bcType === 'nonfan_heat_flux') {
                var powerVal = (typeof facePhys.power === 'number' && isFinite(facePhys.power))
                  ? facePhys.power : 500;
                var eqHeight = span * 0.04445;  // 1U = 44.45mm (gold line 3293)
                var area = eqHeight * rackW;
                var hf = powerVal / Math.max(area, 1e-6);
                faceBc = { bcType: 'nonfan_heat_flux', heatFluxWm2: hf };
              } else {
                faceBc = { bcType: 'wall' };
              }
            } else {
              faceBc = { bcType: 'wall' };
            }
          }

          // ---- materialize bindings per face subdivision (gold 3303-3341) ----
          var patches = (faceDef.patches && faceDef.patches.length) ? faceDef.patches : null;
          var isFlowBc = (faceBc.bcType === 'fan_inlet' || faceBc.bcType === 'fan_outlet');

          if (patches) {
            var nPatches = patches.length;
            for (var pi = 0; pi < nPatches; pi++) {
              var patch = patches[pi];
              var pKey = serverUid + '|' + faceId + '|' + patch.id;
              var pSol = bySource[pKey];
              if (!pSol || !pSol.patchName) continue; // AD9: no export -> skip

              var pBc;
              if (isFlowBc && nPatches > 1) {
                // split flow + power per patch; temperature preserved (ΔT same)
                pBc = {
                  bcType: faceBc.bcType,
                  flowRateM3s: (faceBc.flowRateM3s || 0.0) / nPatches,
                  powerW: (faceBc.powerW || 0.0) / nPatches,
                  temperatureC: faceBc.temperatureC,
                  physicalFlowAreaM2: faceBc.physicalFlowAreaM2,
                  timeVarying: faceBc.timeVarying,
                };
              } else {
                pBc = faceBc;
              }
              var k = emitServerBinding(boundaryBindings, pSol, objectPath, faceId, patch.id, pBc);
              if (pBc.bcType === 'fan_outlet') rackFanOutletKeys.push(k);
            }
            // wall remainder for the subdivided face
            var wKey = serverUid + '|' + faceId + '|__wall__';
            var wSol = bySource[wKey];
            if (wSol && wSol.patchName) {
              emitServerBinding(boundaryBindings, wSol, objectPath, faceId, '__wall__', { bcType: 'wall' });
            }
          } else {
            // whole face — single solid
            var fKey = serverUid + '|' + faceId + '|__face__';
            var fSol = bySource[fKey];
            if (!fSol || !fSol.patchName) return; // AD9: no export -> skip
            var k2 = emitServerBinding(boundaryBindings, fSol, objectPath, faceId, '__face__', faceBc);
            if (faceBc.bcType === 'fan_outlet') rackFanOutletKeys.push(k2);
          }
        });
      });

      // ---- RDHX outlet-temperature override (gold 3418-3470) ----
      var rdhxCfg = rackCons.rdhx;
      if (rdhxCfg && rdhxCfg.enabled === true && rackFanOutletKeys.length > 0) {
        var totalQair = 0.0;
        var totalPower = 0.0;
        Object.keys(servers).forEach(function (su) {
          var s = servers[su] || {};
          var c = s.cooling || {};
          // RDHX sums use the RAW physics power/flow (gold reads s.physics.*,
          // which is the unmodified power/volumeFlowRate before bcMode rewrite).
          // To match, recompute the same P,Q the gold derivation would see: it
          // sums the v1 export's physics.{power,volumeFlowRate}, which for the
          // bcMode rewrites equal the DERIVED P,Q. We mirror per-server.
          var sQ = num(c.volumeFlowRate); if (sQ === undefined) sQ = 0.0;
          var sP = num(c.power); if (sP === undefined) sP = 0.0;
          var mode = (typeof c.serverBCMode === 'string') ? c.serverBCMode : 'powerAndFlow';
          if (mode === 'tempRiseAndFlow') {
            var sdt = num(c.temperatureRise) || 0.0;
            sP = RHO_AIR * AIR_SPECIFIC_HEAT * sQ * sdt;
          } else if (mode === 'powerAndTempRise') {
            var sdt2 = num(c.temperatureRise) || 0.0;
            sQ = sdt2 > 0 ? sP / (RHO_AIR * AIR_SPECIFIC_HEAT * sdt2) : 0.0;
          }
          if (s.type === 'fanServer' || (s.type === 'liquidServer' && resolveServerAirPhysics(s.type, c).type === 'fanServer')) totalQair += sQ;
          totalPower += sP;
        });

        if (totalQair > 0 && totalPower > 0) {
          var tExhaust = ambientTemp + totalPower / (RHO_AIR * AIR_SPECIFIC_HEAT * totalQair);
          var airOutletC = computeRdhxHeatRejection(
            totalQair,
            tExhaust,
            num(rdhxCfg.waterFlowRate) !== undefined ? rdhxCfg.waterFlowRate : 20.0,
            num(rdhxCfg.waterInletTemp) !== undefined ? rdhxCfg.waterInletTemp : 25.0,
            num(rdhxCfg.effectiveness) !== undefined ? rdhxCfg.effectiveness : 0.5
          );
          rackFanOutletKeys.forEach(function (bk) {
            var binding = boundaryBindings[bk];
            if (binding && binding.parameters) binding.parameters.temperatureC = airOutletC;
          });
        }
      }
    });

    return { boundaryBindings: boundaryBindings };
  }

  // FOUR faces — pinned convention (WP-W6, 2026-09-18), and the exclusion must
  // stay even though the V4 server record and the scene builder both know six.
  // The server's left/right faces are INTERFACE WALLS coincident with the rack
  // frame's inner cavity walls: the STL export routes them to
  // merged_server_interface_walls.stl, and neither the v1 manifest
  // (cfdrun_build.js _buildServerFacesExport) nor the stlMap/bySource join
  // carries them. Enumerating them here would emit `wall` bindings for a
  // surface the mesh pipeline never declares, the moment anything starts
  // feeding left/right into bySource. AD9 (no bySource hit -> skip) already
  // kept them out; this list makes the exclusion explicit rather than
  // incidental.
  var FACE_ORDER = ['inlet', 'outlet', 'top', 'bottom'];

  // nonFanFacePhysics — derive the {bcType, temperature, power} a single nonFan
  // face would carry, from the V4 server.cooling settings. Mirrors the exporter's
  // facePhysics() (STL_generate.html:32560-32571) so the gold derivation's inputs
  // are reproduced. powerMode 'unpowered' forces every face to wall.
  function nonFanFacePhysics(cooling, faceId) {
    var powerMode = cooling.powerMode || 'powered';
    var modeKey = faceId === 'inlet' ? 'inletMode' : 'outletMode';
    var heatKey = faceId === 'inlet' ? 'inletHeatType' : 'outletHeatType';
    var tempKey = faceId === 'inlet' ? 'inletTemperature' : 'outletTemperature';
    var powKey = faceId === 'inlet' ? 'inletPower' : 'outletPower';

    var mode = powerMode === 'unpowered' ? 'unpowered' : (cooling[modeKey] || 'powered');
    if (mode === 'unpowered') return { bcType: 'wall', temperature: null, power: null };

    var heatType = cooling[heatKey] || 'fixedTemp';
    if (heatType === 'fixedPower') {
      var pw = num(cooling[powKey]);
      return { bcType: 'nonfan_heat_flux', temperature: null, power: pw !== undefined ? pw : 500 };
    }
    var tmp = num(cooling[tempKey]);
    if (tmp === undefined) tmp = num(cooling.fixedTemperature);
    return { bcType: 'nonfan_fixed_temp', temperature: tmp !== undefined ? tmp : 35.0, power: null };
  }

  // emitServerBinding — write one BoundaryBinding into the dict from a solid join
  // result + a normalized face BC spec. Returns the binding key. Emits ONLY the
  // AD8 fields the BC actually has (omit undefined); timeVarying defaults to {}
  // for fan BCs (mirrors Python's BCSpec timeVarying), matching translateAcus.
  // Domain identity is retained; this resolves only the air transport view.
  function resolveServerAirPhysics(sourceType, cooling) {
    cooling = cooling || {};
    var type = sourceType || 'fanServer';
    var Q = num(cooling.volumeFlowRate), P = num(cooling.power);
    var mode = typeof cooling.serverBCMode === 'string' ? cooling.serverBCMode : 'powerAndFlow';
    var powered = (cooling.powerMode || 'powered') !== 'unpowered';
    if (type === 'liquidServer') {
      if (cooling.volumeFlowRate != null && (Q === undefined || Q < 0))
        throw new Error('liquidServer requires finite nonnegative saved airflow');
      type = powered && Q !== undefined && Q > 0 ? 'fanServer' : 'nonFan';
      if (type === 'fanServer') {
        if (mode !== 'powerAndFlow')
          throw new Error('hybrid fan air export requires saved powerAndFlow');
        if (P === undefined || P < 0)
          throw new Error('hybrid fan air export requires finite nonnegative saved power');
      }
    } else if (type === 'fanServer' && !powered) type = 'nonFan';
    if (sourceType !== 'liquidServer') {
      if (Q === undefined || Q === 0) Q = 0.5;
      if (P === undefined || P === 0) P = 1000;
    }
    var inputQ = Q, inputP = P;
    if (type === 'fanServer' && mode === 'tempRiseAndFlow')
      P = RHO_AIR * AIR_SPECIFIC_HEAT * Q * (num(cooling.temperatureRise) || 0);
    else if (type === 'fanServer' && mode === 'powerAndTempRise') {
      var dt = num(cooling.temperatureRise) || 0;
      Q = dt > 0 ? P / (RHO_AIR * AIR_SPECIFIC_HEAT * dt) : 0;
    }
    return { type: type, sourceType: sourceType || 'fanServer', volumeFlowRate: Q,
      power: P, inputFlow: inputQ, inputPower: inputP, serverBCMode: mode };
  }

  function emitServerBinding(boundaryBindings, sol, objectPath, faceId, patchId, bc) {
    var params = {};
    if (bc.bcType === 'fan_inlet' || bc.bcType === 'fan_outlet') {
      assign(params, 'flowRateM3s', num(bc.flowRateM3s));
      assign(params, 'powerW', num(bc.powerW));
      assign(params, 'temperatureC', num(bc.temperatureC));
      assign(params, 'physicalFlowAreaM2', num(bc.physicalFlowAreaM2));
      params.timeVarying = (bc.timeVarying && typeof bc.timeVarying === 'object') ? bc.timeVarying : {};
    } else if (bc.bcType === 'nonfan_fixed_temp') {
      assign(params, 'temperatureC', num(bc.temperatureC));
    } else if (bc.bcType === 'nonfan_heat_flux') {
      assign(params, 'heatFluxWm2', num(bc.heatFluxWm2));
    }
    // wall: no parameters.

    var bbKey = 'bb_' + sol.patchName;
    boundaryBindings[bbKey] = {
      patchName: sol.patchName,
      solidName: sol.solidName,
      objectPath: objectPath,
      surfaceUid: sol.surfaceUid != null ? sol.surfaceUid : (faceId + '_' + patchId),
      role: bc.bcType,
      bcType: bc.bcType,
      parameters: params,
    };
    return bbKey;
  }

  // computeRdhxHeatRejection — PORT of backend.liquidcooling.devices.rdhx
  // .compute_rdhx_heat_rejection (historical src/dcdtuni/liquid/devices/rdhx.py,
  // commit 47a5cd5). Returns the cooled air_outlet_temp_C only (the single value
  // the bridge derivation overrides fan_outlet with). NOTE: this function uses
  // its OWN air/water constants (1.16, 1006, 4180, ρ_water 998) — distinct from
  // the bridge's rho_air=1.19 / cp=1005 used for T_exhaust.
  function computeRdhxHeatRejection(airFlowM3s, airInletC, waterFlowLpm, waterInletC, effectiveness) {
    var RHO_AIR_RDHX = 1.16;       // kg/m3 (air @ ~30°C)
    var CP_AIR_RDHX = 1006.0;      // J/(kg*K)
    var CP_WATER = 4180.0;         // J/(kg*K)
    var WATER_DENSITY = 998.0;     // kg/m3

    var mAir = RHO_AIR_RDHX * airFlowM3s;            // kg/s
    var mWater = WATER_DENSITY * waterFlowLpm / 60000.0; // kg/s
    var cAir = mAir * CP_AIR_RDHX;                   // W/K
    var cWater = mWater * CP_WATER;                  // W/K
    var cMin = Math.min(cAir, cWater);

    var deltaT = airInletC - waterInletC;
    if (deltaT <= 0 || cMin <= 0) return airInletC;

    var Q = effectiveness * cMin * deltaT;           // W
    return airInletC - Q / Math.max(cAir, 1e-12);    // cooled air outlet °C
  }

  // ----------------------------------------------------------------------------
  // emitPorousBaffleEntry(entry, porousBindings) — SHARED helper (AD10).
  //
  // Emits TWO porousBindings (master + slave) for a baffle-family stlMap entry
  // (kinds: rack_door, floor_opening, floor_remaining, baffle, blanking_panel).
  //
  // Sigma derivation (AD10):
  //   perf = entry.perforation (set by browser/T3 for genuinely-perforated solids)
  //   if !perf || perf.openAreaPct <= 0 → wallBaffle master+slave (no parameters)
  //   else → perforatedBaffle master+slave, sigma=clamp(openAreaPct/100, 0..1),
  //           carry the full perforation block in parameters.
  //
  // NOTE: for rack_door the caller passes DEFAULT_DOOR_PERF as a fallback when
  // entry.perforation is absent (gold default: {shape:'circle',holeDiameterMm:3,
  // openAreaPct:64}). For all other kinds, no default perforation is supplied —
  // absence of entry.perforation means wallBaffle (the browser tags entry.perforation
  // only on genuinely-perforated solids, per AD10/T3 spec).
  //
  // Used by: translateRackExtras (rack_door), translateOpenings (all others).
  // ----------------------------------------------------------------------------
  function producePorousBaffleEntry(entry, porousBindings) {
    var patchName  = entry.patchName;
    var objectPath = entry.objectPath != null ? entry.objectPath : null;
    var surfaceUid = entry.surfaceUid != null ? entry.surfaceUid : patchName;

    var perf = (entry.perforation && typeof entry.perforation === 'object')
      ? entry.perforation : null;
    var openPct = (perf && typeof perf.openAreaPct === 'number' && isFinite(perf.openAreaPct))
      ? perf.openAreaPct : 0;
    var sigma = Math.max(0.0, Math.min(1.0, openPct / 100.0));

    var masterPatch = patchName + '_master';
    var slavePatch  = patchName + '_slave';
    var masterKey   = 'pb_' + patchName + '_master';
    var slaveKey    = 'pb_' + patchName + '_slave';

    if (sigma <= 0) {
      // wall_baffle — no-slip both sides
      porousBindings[masterKey] = {
        patchName: masterPatch,
        objectPath: objectPath,
        surfaceUid: surfaceUid,
        type: 'wallBaffle',
        parameters: {},
      };
      porousBindings[slaveKey] = {
        patchName: slavePatch,
        objectPath: objectPath,
        surfaceUid: surfaceUid,
        type: 'wallBaffle',
        parameters: {},
      };
    } else {
      // perforated_baffle — carry full perforation block (AD10)
      var pbParams = {
        sigma: sigma,
        perforation: perf,
      };
      porousBindings[masterKey] = {
        patchName: masterPatch,
        objectPath: objectPath,
        surfaceUid: surfaceUid,
        type: 'perforatedBaffle',
        parameters: pbParams,
      };
      porousBindings[slaveKey] = {
        patchName: slavePatch,
        objectPath: objectPath,
        surfaceUid: surfaceUid,
        type: 'perforatedBaffle',
        parameters: pbParams,
      };
    }
  }

  // Same current producer, with owned input/output at the export-solid boundary.
  function emitPorousBaffleEntry(entry, porousBindings) {
    var owned = structuredClone(entry), resolved = {};
    producePorousBaffleEntry(owned, resolved);
    Object.keys(resolved).forEach(function(key) { porousBindings[key] = resolved[key]; });
  }

  function resolveAirSurfaceFragment(solidName, entry) {
    if (typeof solidName !== 'string' || !solidName) throw new Error('Air surface fragment needs exported solid name');
    if (!entry || !(Object.prototype.hasOwnProperty.call(OPENING_POROUS_KINDS, entry.kind) || entry.kind === 'room_wall' || entry.kind === 'partition_wall'))
      throw new Error('Unsupported air surface fragment kind: ' + (entry && entry.kind));
    if (entry.patchName != null && typeof entry.patchName !== 'string') throw new Error('Air surface patchName must be a string');
    var owned = structuredClone(entry), map = Object.create(null);
    map[solidName] = owned;
    var resolved = translateOpenings(null, {stlMap: map});
    return {
      schema: 'dcdtuni.air-surface-fragment/v1', sourceScope: 'export-solid',
      sourceSolidName: solidName, sourceEntry: structuredClone(entry),
      boundaryBindings: resolved.boundaryBindings, porousBindings: resolved.porousBindings, sourceTerms: {}
    };
  }

  // ----------------------------------------------------------------------------
  // translateRackExtras(state, ctx) -> { boundaryBindings: {...}, porousBindings: {...} }
  //   (P3 T2c-2)
  //
  // Emits BoundaryBinding entries for the rack's NON-server solids:
  //   kind:'frame'          -> wall boundaryBinding   (no parameters)
  //   kind:'rack_partition' -> wall boundaryBinding   (no parameters)
  //   kind:'through_zone'   -> through_zone boundaryBinding  (parameters.sigma)
  //   kind:'rack_door'      -> TWO porousBindings (master+slave) via emitPorousBaffleEntry.
  //                            Default perforation {circle,3,64} applied when entry.perforation
  //                            is absent (rack_door only — no default for other baffle kinds).
  //
  // Input: ctx.stlMap — comprehensive stlMap (AD9) keyed by solidName.
  // Each entry: { solidName, patchName, stlFileId, objectPath, surfaceUid, kind,
  //               source:{modelUid,...}, perforation?, sigma? }.
  //
  // Through-zone sigma priority (AD10):
  //   1. entry.sigma (explicit on the stlMap entry)
  //   2. state.constructions.racks[source.modelUid].throughSlotSigma
  //   3. 0.05 (default, gold line stlgen_runner.py:3479)
  //
  // Door sigma: DEFAULT_DOOR_PERF = {shape:'circle',holeDiameterMm:3,openAreaPct:64}
  //   perf = entry.perforation || DEFAULT_DOOR_PERF  (rack_door default only)
  //   sigma = clamp(perf.openAreaPct/100, 0..1)
  //   sigma <= 0 -> wallBaffle master+slave
  //   sigma >  0 -> perforatedBaffle master+slave (carry perforation block)
  //
  // Gold reference: _build_bridge_bc stlgen_runner.py:3472-3714 (collapsed under Arch A).
  // Entries with unknown kind or no patchName are silently skipped (AD9).
  // ----------------------------------------------------------------------------
  var DEFAULT_DOOR_PERF = { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };

  function translateRackExtras(state, ctx) {
    var boundaryBindings = {};
    var porousBindings = {};
    var stlMap = (ctx && ctx.stlMap) || {};
    var cons = (state && state.constructions) || {};
    var racks = cons.racks || {};

    Object.keys(stlMap).forEach(function (solidName) {
      var entry = stlMap[solidName] || {};
      var kind = entry.kind;
      var patchName = entry.patchName;

      // AD9: skip if no patchName or kind is not one we handle
      if (!patchName) return;

      var objectPath = entry.objectPath != null ? entry.objectPath
        : ('constructions.racks.' + ((entry.source && entry.source.modelUid) || solidName));
      var surfaceUid = entry.surfaceUid != null ? entry.surfaceUid : solidName;

      if (kind === 'frame' || kind === 'rack_partition') {
        // wall — no parameters
        boundaryBindings['bb_' + patchName] = {
          patchName: patchName,
          solidName: entry.solidName != null ? entry.solidName : solidName,
          objectPath: objectPath,
          surfaceUid: surfaceUid,
          role: 'wall',
          bcType: 'wall',
          parameters: {},
        };

      } else if (kind === 'through_zone') {
        // Resolve sigma: entry > state rack.throughSlotSigma > 0.05 default
        var sigma;
        if (entry.sigma != null && typeof entry.sigma === 'number' && isFinite(entry.sigma)) {
          sigma = entry.sigma;
        } else {
          var modelUid = entry.source && entry.source.modelUid;
          var rack = modelUid ? (racks[modelUid] || {}) : {};
          var ts = rack.throughSlotSigma;
          if (ts != null && typeof ts === 'number' && isFinite(ts)) {
            sigma = ts;
          } else {
            sigma = 0.05;
          }
        }
        boundaryBindings['bb_' + patchName] = {
          patchName: patchName,
          solidName: entry.solidName != null ? entry.solidName : solidName,
          objectPath: objectPath,
          surfaceUid: surfaceUid,
          role: 'through_zone',
          bcType: 'through_zone',
          parameters: { sigma: sigma },
        };

      } else if (kind === 'rack_door') {
        // AD10: read perforation from entry (set by browser caller/T3), else DEFAULT_DOOR_PERF.
        // rack_door is the ONLY baffle kind with a default perforation block.
        var doorEntry = entry;
        if (!entry.perforation || typeof entry.perforation !== 'object') {
          // Provide the rack_door default so emitPorousBaffleEntry sees openAreaPct=64
          doorEntry = {};
          Object.keys(entry).forEach(function (k) { doorEntry[k] = entry[k]; });
          doorEntry.perforation = DEFAULT_DOOR_PERF;
          doorEntry.objectPath = objectPath;
          doorEntry.surfaceUid = surfaceUid;
        }
        emitPorousBaffleEntry(doorEntry, porousBindings);

      }
      // else: unknown kind — silently skip (other translators handle them)
    });

    return { boundaryBindings: boundaryBindings, porousBindings: porousBindings };
  }

  // ----------------------------------------------------------------------------
  // translateOpenings(state, ctx) -> { boundaryBindings: {...}, porousBindings: {...} }
  //   (P3 T2d-1)
  //
  // Emits bindings for floor/baffle/blanking/wall solids. Iterates ctx.stlMap
  // and dispatches by kind (AD9):
  //
  //   'floor_opening'    -> emitPorousBaffleEntry (perforation → perforated/wallBaffle)
  //   'floor_remaining'  -> emitPorousBaffleEntry (perforation → perforated/wallBaffle)
  //   'baffle'           -> emitPorousBaffleEntry (perforation → perforated/wallBaffle)
  //   'blanking_panel'   -> emitPorousBaffleEntry (perforation → perforated/wallBaffle)
  //   'room_wall'        -> boundaryBinding bb_* { bcType:'wall', role:'wall' }
  //   'partition_wall'   -> boundaryBinding bb_* { bcType:'wall', role:'wall' }
  //   anything else      -> silently skip (other translators own those kinds)
  //
  // Default perforation: NONE for any of these kinds. The browser (T3) sets
  // entry.perforation only for genuinely-perforated solids; absent perforation
  // means wallBaffle (contrast: rack_door has its own default in translateRackExtras).
  //
  // Gold reference: _build_bridge_bc stlgen_runner.py:3499-3770 (Arch A collapse).
  // Entries with no patchName are silently skipped (AD9).
  // ----------------------------------------------------------------------------
  var OPENING_POROUS_KINDS = {
    floor_opening: true,
    floor_remaining: true,
    baffle: true,
    blanking_panel: true,
  };

  function translateOpenings(state, ctx) {
    var boundaryBindings = {};
    var porousBindings = {};
    var stlMap = (ctx && ctx.stlMap) || {};

    Object.keys(stlMap).forEach(function (solidName) {
      var entry = stlMap[solidName] || {};
      var kind = entry.kind;
      var patchName = entry.patchName;

      // AD9: skip if no patchName
      if (!patchName) return;

      var objectPath = entry.objectPath != null ? entry.objectPath : null;
      var surfaceUid = entry.surfaceUid != null ? entry.surfaceUid : patchName;

      if (OPENING_POROUS_KINDS[kind]) {
        // Porous baffle family — delegate to shared helper.
        // No default perforation for these kinds (contrast with rack_door).
        var e = entry;
        if (objectPath !== entry.objectPath || surfaceUid !== entry.surfaceUid) {
          // Ensure objectPath/surfaceUid are resolved on the entry copy.
          e = {};
          Object.keys(entry).forEach(function (k) { e[k] = entry[k]; });
          e.objectPath = objectPath;
          e.surfaceUid = surfaceUid;
        }
        emitPorousBaffleEntry(e, porousBindings);

      } else if (kind === 'room_wall' || kind === 'partition_wall') {
        // Simple wall boundary binding — no parameters (gold :3757-3770)
        boundaryBindings['bb_' + patchName] = {
          patchName: patchName,
          solidName: entry.solidName != null ? entry.solidName : solidName,
          objectPath: objectPath,
          surfaceUid: surfaceUid,
          role: 'wall',
          bcType: 'wall',
          parameters: {},
        };

      }
      // else: unknown or other-translator kind (rack_server, acu, frame, etc.) — skip
    });

    return { boundaryBindings: boundaryBindings, porousBindings: porousBindings };
  }

  // ----------------------------------------------------------------------------
  // translateThermalExtras(state, ctx) -> { boundaryBindings: {...}, sourceTerms: {...} }
  //   (P3 T2d-2)
  //
  // Emits thermal boundary bindings and porous-zone source terms for:
  //
  //   'wall_object'     -> boundaryBinding bb_<patchName>  bcType:'fixedTemperature'
  //                        T = entry.physics.fixedTemperature if set, else
  //                        ambient + heatPower / (10 * area),  area = 2*(WD+WH+DH)
  //                        from entry.objSize.{W,D,H}.  Skip if T is null.
  //
  //   'heat_source'     -> boundaryBinding bb_<patchName>  per heat mode:
  //                        heatMode='temperature' + fixedTemperature set
  //                          -> bcType:'fixedTemperature', temperatureC
  //                        heatMode='power'       + totalPower > 0
  //                          -> bcType:'heat_source_flux',
  //                             heatFluxWm2 = totalPower / area  (area = 2*(WD+WH+DH)
  //                             from entry.size.{W,D,H}, 0 if area <= 1e-8),
  //                             surfaceAreaM2, timeVarying
  //                        else -> SKIP.
  //
  //   'partition_hole'  -> sourceTerm st_<cellzoneName>  type:'porousZone'
  //                        GATE: entry.leakageRate < 100.
  //                        sigma = clamp(leakageRate/100, 0..1)
  //                        Darcy-Forchheimer (min_depth=0.001, cd=0.6):
  //                          s = clamp(sigma, 0.01..0.99)
  //                          depth from entry.depth (clamped >= 0.001)
  //                          f = (1/depth) * ((1/(cd*s)) - 1)^2
  //                          d = 0
  //                        Geometry (origin/e1/e2) PRE-COMPUTED on the stlMap entry
  //                        (Arch A: translator does only the physics).
  //
  //   all other kinds   -> silently skip.
  //
  // Gold references:
  //   wall_object  : stlgen_runner.py:3772-3778 + resolve_wall_object_temp :3132-3149
  //   heat_source  : stlgen_runner.py:3780-3801
  //   partition_hole: _build_partition_hole_fvoptions_config :1522-1595
  //                   + _darcy_forchheimer :228-246 called with min_depth=0.001
  //
  // NOTE for T4/T5: the porousZone sourceTerm parameters shape is:
  //   { sigma, d:[dCoeff,0,0], f:[fCoeff,0,0], origin:[x,y,z], e1:[x,y,z], e2:[x,y,z] }
  //   T4 must declare these on PorousZoneParameters (all camelCase, all optional).
  //   T5's reader materializes them into fvOptions inputs.
  // ----------------------------------------------------------------------------

  // Partition-hole Darcy-Forchheimer coefficients (gold _darcy_forchheimer, min_depth=0.001)
  function darcyForchheimer(sigma, depth) {
    var MIN_DEPTH = 0.001;
    var s = Math.max(0.01, Math.min(0.99, sigma));
    if (depth < MIN_DEPTH) depth = MIN_DEPTH;
    var cd = 0.6;
    var fCoeff = (1.0 / depth) * Math.pow((1.0 / (cd * s)) - 1.0, 2);
    var dCoeff = 0.0;
    return { d: dCoeff, f: fCoeff };
  }

  // Resolve the wall-object temperature (gold resolve_wall_object_temp, :3132-3149).
  // Returns a finite number, or null to signal "skip".
  function resolveWallObjectTemp(ambient, physics, objSize) {
    var p = physics || {};
    var fixedTemp = p.fixedTemperature;
    if (fixedTemp != null && typeof fixedTemp === 'number' && isFinite(fixedTemp)) {
      return fixedTemp;
    }
    var power = (typeof p.heatPower === 'number' && isFinite(p.heatPower)) ? p.heatPower : 0.0;
    if (power <= 0) return null;
    var sz = objSize || {};
    var W = (typeof sz.W === 'number' && isFinite(sz.W)) ? sz.W : 0.0;
    var D = (typeof sz.D === 'number' && isFinite(sz.D)) ? sz.D : 0.0;
    var H = (typeof sz.H === 'number' && isFinite(sz.H)) ? sz.H : 0.0;
    var area = 2.0 * (W * D + W * H + D * H);
    if (area <= 1e-8) return null;
    return ambient + power / (10.0 * area);
  }

  // Compute bounding-box surface area 2*(WD+WH+DH) for a size object {W,D,H}.
  function boxSurfaceArea(size) {
    var sz = size || {};
    var W = (typeof sz.W === 'number' && isFinite(sz.W)) ? sz.W : 0.0;
    var D = (typeof sz.D === 'number' && isFinite(sz.D)) ? sz.D : 0.0;
    var H = (typeof sz.H === 'number' && isFinite(sz.H)) ? sz.H : 0.0;
    return 2.0 * (W * D + W * H + D * H);
  }

  function translateThermalExtras(state, ctx) {
    var boundaryBindings = {};
    var sourceTerms = {};
    var st = state || {};
    var stlMap = (ctx && ctx.stlMap) || {};

    // ambient temperature (for wall-object power→temp derivation; default 22)
    var ambient = (st.geometry && typeof st.geometry.temperature === 'number'
      && isFinite(st.geometry.temperature))
      ? st.geometry.temperature : 22.0;

    Object.keys(stlMap).forEach(function (solidName) {
      var entry = stlMap[solidName] || {};
      var kind = entry.kind;

      if (kind === 'wall_object') {
        // patchName required for boundary binding (AD9: skip if absent)
        var patchName = entry.patchName;
        if (!patchName) return;

        var T = resolveWallObjectTemp(ambient, entry.physics, entry.objSize);
        if (T === null) return; // skip conditions (power<=0, area<=1e-8, no fixed temp)

        var bbKey = 'bb_' + patchName;
        boundaryBindings[bbKey] = {
          patchName: patchName,
          solidName: entry.solidName != null ? entry.solidName : solidName,
          objectPath: entry.objectPath != null ? entry.objectPath : null,
          surfaceUid: entry.surfaceUid != null ? entry.surfaceUid : patchName,
          role: 'fixed_temperature',
          bcType: 'fixedTemperature',
          parameters: { temperatureC: T },
        };

      } else if (kind === 'heat_source') {
        // patchName required (AD9)
        var patchName = entry.patchName;
        if (!patchName) return;

        var heatMode = (typeof entry.heatMode === 'string') ? entry.heatMode : 'power';
        var bbKey = 'bb_' + patchName;
        var bbBase = {
          patchName: patchName,
          solidName: entry.solidName != null ? entry.solidName : solidName,
          objectPath: entry.objectPath != null ? entry.objectPath : null,
          surfaceUid: entry.surfaceUid != null ? entry.surfaceUid : patchName,
          role: 'heat_source_flux',
        };

        if (heatMode === 'temperature') {
          var fixedT = entry.fixedTemperature;
          if (fixedT == null || typeof fixedT !== 'number' || !isFinite(fixedT)) return;
          bbBase.role = 'fixed_temperature';
          bbBase.bcType = 'fixedTemperature';
          bbBase.parameters = { temperatureC: fixedT };
          boundaryBindings[bbKey] = bbBase;

        } else if (heatMode === 'power') {
          var totalPower = (typeof entry.totalPower === 'number' && isFinite(entry.totalPower))
            ? entry.totalPower : 0.0;
          if (totalPower <= 0) return;
          var area = (typeof entry.thermalSurfaceAreaM2 === 'number' && isFinite(entry.thermalSurfaceAreaM2))
            ? entry.thermalSurfaceAreaM2 : boxSurfaceArea(entry.size);
          var flux = (area > 1e-8) ? totalPower / area : 0.0;
          var surfArea = (area > 1e-8) ? area : 0.0;
          var tv = (entry.timeVarying && typeof entry.timeVarying === 'object')
            ? entry.timeVarying : {};
          bbBase.bcType = 'heat_source_flux';
          var hsParams = { heatFluxWm2: flux, surfaceAreaM2: surfArea, timeVarying: tv };
          bbBase.parameters = hsParams;
          boundaryBindings[bbKey] = bbBase;

        }
        // else: unknown heatMode → skip (AD9)

      } else if (kind === 'partition_hole') {
        // Partition holes key on cellzoneName, not patchName.
        var cellzoneName = (typeof entry.cellzoneName === 'string' && entry.cellzoneName)
          ? entry.cellzoneName : null;
        if (!cellzoneName) return; // can't place without a zone name

        // Gate: leakageRate < 100
        var lr = (typeof entry.leakageRate === 'number' && isFinite(entry.leakageRate))
          ? entry.leakageRate : 100.0;
        if (lr >= 100.0) return;

        var sigma = Math.max(0.0, Math.min(1.0, lr / 100.0));
        var depth = (typeof entry.depth === 'number' && isFinite(entry.depth) && entry.depth > 0)
          ? entry.depth : 0.001;

        var df = darcyForchheimer(sigma, depth);

        // Geometry pre-computed by T3/stlMap builder (Arch A).
        var origin = Array.isArray(entry.origin) ? entry.origin : [0, 0, 0];
        var e1 = Array.isArray(entry.e1) ? entry.e1 : [0, 0, 1];
        var e2 = Array.isArray(entry.e2) ? entry.e2 : [0, 1, 0];

        var objectPath = entry.objectPath != null ? entry.objectPath : null;

        var stKey = 'st_' + cellzoneName;
        sourceTerms[stKey] = {
          type: 'porousZone',
          objectPath: objectPath,
          region: cellzoneName,
          parameters: {
            sigma: sigma,
            d: [df.d, 0, 0],
            f: [df.f, 0, 0],
            origin: origin,
            e1: e1,
            e2: e2,
          },
        };

      }
      // else: other kind → silently skip (other translators own those)
    });

    return { boundaryBindings: boundaryBindings, sourceTerms: sourceTerms };
  }

  // ----------------------------------------------------------------------------
  // translateMonitors(state) -> { postprocessConfig: {...} }  (P3 T2e)
  //
  // Emits the postprocessConfig section of the V2 manifest (schema §10):
  //   probes         : from state.instrumentation.probes (uid-keyed dict of probe objects)
  //   surfaceMonitors: from state.runDefaults.simulationConfig.monitoring (if present),
  //                    else {}
  //   outputFields   : ['T','U','p_rgh'] by default; override from sim config if present
  //   labels         : { sensorMeasurements: {...} } from state.instrumentation.measurements
  //
  // V4 confirmed paths (inspected against migrated Base_leakage, 2026-06-14):
  //   probes   : state.instrumentation.probes[uid] = {uid, geometry:{location:{x,y,z}},
  //              meta:{name,enabled}, name?}
  //   monitoring: state.runDefaults.simulationConfig.monitoring — ABSENT in Base_leakage
  //              (most real projects have no monitoring section → emit {})
  //   measurements: state.instrumentation.measurements (same as assembleSensorMeasurements)
  //
  // Tolerates missing instrumentation / runDefaults at every level.
  // Pure — no DOM, no Three.js.
  // ----------------------------------------------------------------------------
  var DEFAULT_OUTPUT_FIELDS = ['T', 'U', 'p_rgh'];

  function translateMonitors(state) {
    var st = state || {};
    var instr = st.instrumentation || {};
    var rawProbes = instr.probes || {};

    // ---- probes: map each uid → {name, location, enabled} ----
    var probes = {};
    Object.keys(rawProbes).forEach(function (uid) {
      var p = rawProbes[uid] || {};
      var meta = p.meta || {};
      var geom = p.geometry || {};
      var loc = geom.location || {};

      // name: prefer p.name, then meta.name, then uid
      var name = (typeof p.name === 'string' && p.name) ? p.name
        : ((typeof meta.name === 'string' && meta.name) ? meta.name : uid);

      // location: {x, y, z} from geometry.location
      var location = {
        x: typeof loc.x === 'number' ? loc.x : 0,
        y: typeof loc.y === 'number' ? loc.y : 0,
        z: typeof loc.z === 'number' ? loc.z : 0,
      };

      // enabled: from meta.enabled (default true when absent)
      var enabled = typeof meta.enabled === 'boolean' ? meta.enabled : true;

      probes[uid] = { name: name, location: location, enabled: enabled };
    });

    // ---- surfaceMonitors: from runDefaults.simulationConfig.monitoring (if any) ----
    var surfaceMonitors = {};
    var rd = st.runDefaults || {};
    var sc = rd.simulationConfig || {};
    var monitoringCfg = sc.monitoring;
    if (monitoringCfg && typeof monitoringCfg === 'object') {
      // The monitoring config is a dict of surface monitor objects keyed by uid.
      // Echo it as-is: the schema only requires Record<uid,object>; the exact shape
      // of each monitor entry is opaque to this translator.
      Object.keys(monitoringCfg).forEach(function (uid) {
        var mon = monitoringCfg[uid];
        if (mon && typeof mon === 'object') {
          surfaceMonitors[uid] = mon;
        }
      });
    }

    // ---- outputFields: default ['T','U','p_rgh']; override if sim config declares them ----
    var outputFields = DEFAULT_OUTPUT_FIELDS;
    if (Array.isArray(sc.outputFields) && sc.outputFields.length > 0) {
      outputFields = sc.outputFields;
    }

    // ---- labels.sensorMeasurements: from instrumentation.measurements ----
    var sensorMeasurements = assembleSensorMeasurements(st);

    return {
      postprocessConfig: {
        probes: probes,
        surfaceMonitors: surfaceMonitors,
        outputFields: outputFields,
        labels: { sensorMeasurements: sensorMeasurements },
      },
    };
  }

  // ----------------------------------------------------------------------------
  // buildManifest(state, stlMap, exportedAt) -> Promise<CfdrunV2>  (P3 T2f, CAPSTONE)
  //
  // The orchestrator the browser (T3) calls. Steps (brief §"What to build"):
  //   1. start from cfdrunSchema.emptyManifest()
  //   2. build the bySource index from stlMap (AD7 join key + __face__/__wall__
  //      sentinels are already encoded by T3 into source.patchId — concatenate)
  //   3. assembleGeometryExport (§4)
  //   4. ctx = { geometryExport, stlMap, bySource }
  //   5. assembleInputs (§3)
  //   6. await buildSourceProject (§2 + AD4)
  //   7. name + a deterministic runId (derived from exportedAt; no Date.now/random)
  //   8. merge every binding/source-term/porous fragment (Object.assign — uids are
  //      unique so no clobber)
  //   9. postprocessConfig from translateMonitors
  //  10. mirror run config (declared-only) — deep-copied so the manifest never
  //      aliases live state
  //  11. validateManifest → throw on any structural error
  //  12. return the manifest
  // ----------------------------------------------------------------------------
  function buildManifest(state, stlMap, exportedAt) {
    var st = state || {};
    // A saved strict declaration is not a compiled source-topology graph.
    // Fail before deriving any run payload in either the HTML or React editor.
    if (st.geometryTopology !== undefined) {
      throw new Error('geometryTopology strict V1 is not yet compiled and validated against source geometry; .cfdrun export and air runs are blocked');
    }
    var m = schema.emptyManifest();

    // 2. bySource index (AD7). Each stlMap entry whose source carries a non-null
    //    modelUid + faceId + patchId becomes a join target. T3 already encodes the
    //    __face__/__wall__ sentinels into source.patchId; we just concatenate.
    var bySource = {};
    if (stlMap && typeof stlMap === 'object') {
      Object.keys(stlMap).forEach(function (solidName) {
        var entry = stlMap[solidName] || {};
        var src = entry.source;
        if (src && src.modelUid != null && src.faceId != null && src.patchId != null) {
          bySource[src.modelUid + '|' + src.faceId + '|' + src.patchId] = entry;
        }
      });
    }

    // 3. geometryExport
    m.geometryExport = assembleGeometryExport(st, stlMap);

    // 4. translator context
    var ctx = { geometryExport: m.geometryExport, stlMap: stlMap || {}, bySource: bySource };

    // 5. inputs
    m.inputs = assembleInputs(st);

    // 6. sourceProject (async — contentHash via WebCrypto)
    return buildSourceProject(st, exportedAt).then(function (sourceProject) {
      m.sourceProject = sourceProject;

      // 7. name + runId. runId is derived from exportedAt so it stays deterministic
      //    (the node test harness has no Date.now/Math.random; the browser passes
      //    exportedAt). Empty string when exportedAt is absent.
      m.name = st.name || (st.meta && st.meta.name) || '';
      m.runId = (exportedAt != null && exportedAt !== '') ? ('run_' + exportedAt) : '';

      // 8. merge binding / source-term / porous fragments from every translator.
      //    uids are unique across translators (patchName-derived) so Object.assign
      //    merges without clobber.
      mergeFragment(translateAcus(st, ctx), m);
      mergeFragment(translateRackServers(st, ctx), m);
      mergeFragment(translateRackExtras(st, ctx), m);
      mergeFragment(translateOpenings(st, ctx), m);
      mergeFragment(translateThermalExtras(st, ctx), m);

      // 9. postprocessConfig
      var mon = translateMonitors(st);
      m.postprocessConfig = mon.postprocessConfig;

      // 10. mirror run config (declared-only pass-through, deep-copied so the
      //     manifest does not alias live state).
      var rd = st.runDefaults || {};
      m.meshConfig = (rd.meshConfig && typeof rd.meshConfig === 'object')
        ? deepCopy(rd.meshConfig) : {};
      m.simulationConfig = (rd.simulationConfig && typeof rd.simulationConfig === 'object')
        ? deepCopy(rd.simulationConfig) : {};

      // 11. validate
      var errs = schema.validateManifest(m);
      if (errs.length) {
        throw new Error('cfdrun manifest invalid: ' + errs.join('; '));
      }

      // 12. done
      return m;
    });
  }

  // mergeFragment — shallow-merge a translator fragment's binding sub-dicts into
  // the manifest. Only the keys a translator actually returns are merged.
  function mergeFragment(fragment, manifest) {
    if (!fragment || typeof fragment !== 'object') return;
    if (fragment.boundaryBindings) Object.assign(manifest.boundaryBindings, fragment.boundaryBindings);
    if (fragment.porousBindings) Object.assign(manifest.porousBindings, fragment.porousBindings);
    if (fragment.sourceTerms) Object.assign(manifest.sourceTerms, fragment.sourceTerms);
  }

  // deepCopy — JSON round-trip so the mirrored run config never aliases live
  // state. Run config is plain JSON-serializable data (no functions/Three.js).
  function deepCopy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  // ---- dual export: browser window + Node.js module.exports ----
  var api = {
    sanitizeName: sanitizeName,
    buildManifest: buildManifest,
    assembleInputs: assembleInputs,
    assertUniqueServerUids: assertUniqueServerUids,
    assembleGeometryExport: assembleGeometryExport,
    buildSourceProject: buildSourceProject,
    translateAcus: translateAcus,
    translateRackServers: translateRackServers,
    resolveServerAirPhysics: resolveServerAirPhysics,
    translateRackExtras: translateRackExtras,
    translateOpenings: translateOpenings,
    translateThermalExtras: translateThermalExtras,
    translateMonitors: translateMonitors,
    // exposed for downstream T2b–T2f reuse + targeted testing:
    computeAreaM2: computeAreaM2,
    computeRdhxHeatRejection: computeRdhxHeatRejection,
    stableStringify: stableStringify,
    projectContentHashSync: projectContentHashSync,
    canonicalProjectForHash: canonicalProjectForHash,
    sha256TextSync: sha256TextSync,
    emitPorousBaffleEntry: emitPorousBaffleEntry,
    resolveAirSurfaceFragment: resolveAirSurfaceFragment,
    // thermal helpers exposed for targeted testing:
    darcyForchheimer: darcyForchheimer,
    resolveWallObjectTemp: resolveWallObjectTemp,
  };
  if (typeof window !== 'undefined') window.cfdrunExport = api;
  if (typeof globalThis !== 'undefined') globalThis.cfdrunExport = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
