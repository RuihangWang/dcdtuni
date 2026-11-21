# 3D Object Snap Algorithm Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-HTML snap implementation with a standalone, jitter-free, scalable `web/js/snap.js` module that supports face/edge/vertex/rotation snap with a hysteresis state machine and 3D spatial-grid broad phase, fixing baffle drag jitter for rotated objects.

**Architecture:** Create a single new file `STLGeneratorForOpenFoam/web/js/snap.js` exposing one factory `createSnapModule(deps)` that returns a small API (setConfig / beginDrag / updateDrag / endDrag / getHighlight / invalidate). Replace the two existing call sites of `computeProximitySnap` in `STL_generate.html` with calls into the new module. Add four toolbar toggles (F / E / V / R) with `localStorage` persistence. Use Node's built-in `node:test` runner for unit tests (no `package.json` / npm dependency added). Keep grid snap untouched (it is orthogonal to this redesign).

**Tech Stack:** Vanilla JavaScript ES2017+ (browser), Three.js r128 (already loaded), Node.js ≥18 built-in `node:test` for unit tests, hand-rolled THREE-mock for tests (avoids npm setup).

**Spec:** [docs/superpowers/specs/2026-04-14-snap-algorithm-design.md](../specs/2026-04-14-snap-algorithm-design.md)

---

## File Structure

| Path | Role |
|---|---|
| `STLGeneratorForOpenFoam/web/js/snap.js` (NEW) | Snap module, UMD-style export (browser global + CommonJS for tests) |
| `STLGeneratorForOpenFoam/web/js/snap.test.js` (NEW) | Unit tests run via `node --test` |
| `STLGeneratorForOpenFoam/web/js/three-mock.js` (NEW) | Minimal THREE shim for tests (Vector3 / Quaternion / Box3) |
| `STLGeneratorForOpenFoam/web/STL_generate.html` (MODIFIED) | Load snap.js, wire toolbar, replace computeProximitySnap call sites, add migration |

Lines to remove from `STL_generate.html` after Task 14 cuts over:
- `computeProximitySnap` body — ~L12545-L12652
- `initSnapCache` + `querySnapCandidates` + cache state — ~L12468-L12543
- Cumulative snap application — `state.snapFace || state.snapEdge` blocks at L15367-L15377 and L16427-L16432
- Old toolbar handlers `toggleSnapFace` / `toggleSnapEdge` — L12436-L12448
- `state.snapFace` / `state.snapEdge` field reads (after migration in Task 15)

---

## Conventions for the engineer

- All commits live on the current branch. Do NOT create new branches.
- Each commit footer must include `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`.
- Use absolute paths in commands.
- Server restart command (use ONLY when integration testing requires the new file served): `fuser -k 8005/tcp 2>/dev/null; conda run -n dcdtuni python /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/server.py --port 8005 --ws-port 8006 --case-dir case/Tri_angle &`.
- Never `kill`, `pkill`, `killall`, `xargs kill`, or `pgrep`.
- Read the spec section corresponding to each task before starting — sections are referenced.

---

## Task 1: Bootstrap Node test runner + minimal THREE mock

**Why first:** Every TDD task below depends on a runnable test infrastructure. `node:test` is built into Node ≥18, so we avoid adding `package.json` / npm. The THREE mock provides just enough of `Vector3` / `Quaternion` / `Box3` for the snap module's needs.

**Files:**
- Create: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/three-mock.js`
- Create: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Verify Node ≥18 and `node:test` availability**

```bash
node --version
node -e "require('node:test'); console.log('ok');"
```

Expected: version ≥ 18, output `ok`.

- [ ] **Step 2: Write the THREE mock (minimal vector + quaternion + box)**

Create `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/three-mock.js`:

```javascript
'use strict';

class Vector3 {
    constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
    set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
    clone() { return new Vector3(this.x, this.y, this.z); }
    copy(v) { this.x = v.x; this.y = v.y; this.z = v.z; return this; }
    add(v) { this.x += v.x; this.y += v.y; this.z += v.z; return this; }
    sub(v) { this.x -= v.x; this.y -= v.y; this.z -= v.z; return this; }
    addScaledVector(v, s) { this.x += v.x * s; this.y += v.y * s; this.z += v.z * s; return this; }
    multiplyScalar(s) { this.x *= s; this.y *= s; this.z *= s; return this; }
    dot(v) { return this.x * v.x + this.y * v.y + this.z * v.z; }
    cross(v) {
        const x = this.y * v.z - this.z * v.y;
        const y = this.z * v.x - this.x * v.z;
        const z = this.x * v.y - this.y * v.x;
        this.x = x; this.y = y; this.z = z; return this;
    }
    length() { return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z); }
    lengthSq() { return this.x * this.x + this.y * this.y + this.z * this.z; }
    normalize() { const l = this.length(); return l > 0 ? this.multiplyScalar(1 / l) : this; }
    distanceTo(v) { const dx = this.x - v.x, dy = this.y - v.y, dz = this.z - v.z; return Math.sqrt(dx * dx + dy * dy + dz * dz); }
    distanceToSquared(v) { const dx = this.x - v.x, dy = this.y - v.y, dz = this.z - v.z; return dx * dx + dy * dy + dz * dz; }
    applyQuaternion(q) {
        const x = this.x, y = this.y, z = this.z;
        const qx = q.x, qy = q.y, qz = q.z, qw = q.w;
        const ix = qw * x + qy * z - qz * y;
        const iy = qw * y + qz * x - qx * z;
        const iz = qw * z + qx * y - qy * x;
        const iw = -qx * x - qy * y - qz * z;
        this.x = ix * qw + iw * -qx + iy * -qz - iz * -qy;
        this.y = iy * qw + iw * -qy + iz * -qx - ix * -qz;
        this.z = iz * qw + iw * -qz + ix * -qy - iy * -qx;
        return this;
    }
}

class Quaternion {
    constructor(x = 0, y = 0, z = 0, w = 1) { this.x = x; this.y = y; this.z = z; this.w = w; }
    clone() { return new Quaternion(this.x, this.y, this.z, this.w); }
    setFromAxisAngle(axis, angle) {
        const half = angle / 2, s = Math.sin(half);
        this.x = axis.x * s; this.y = axis.y * s; this.z = axis.z * s; this.w = Math.cos(half);
        return this;
    }
    setFromUnitVectors(from, to) {
        // Shortest-arc quaternion rotating `from` onto `to`. Both must be unit.
        let r = from.dot(to) + 1;
        const v = new Vector3();
        if (r < 1e-6) {
            // 180°: pick orthogonal axis
            r = 0;
            if (Math.abs(from.x) > Math.abs(from.z)) v.set(-from.y, from.x, 0);
            else                                    v.set(0, -from.z, from.y);
        } else {
            v.copy(from).cross(to);
        }
        this.x = v.x; this.y = v.y; this.z = v.z; this.w = r;
        const len = Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z + this.w * this.w);
        if (len > 0) { this.x /= len; this.y /= len; this.z /= len; this.w /= len; }
        return this;
    }
    toArray() { return [this.x, this.y, this.z, this.w]; }
}

class Box3 {
    constructor(min, max) {
        this.min = min || new Vector3(+Infinity, +Infinity, +Infinity);
        this.max = max || new Vector3(-Infinity, -Infinity, -Infinity);
    }
    clone() { return new Box3(this.min.clone(), this.max.clone()); }
    setFromPoints(points) {
        this.min.set(+Infinity, +Infinity, +Infinity);
        this.max.set(-Infinity, -Infinity, -Infinity);
        for (const p of points) {
            if (p.x < this.min.x) this.min.x = p.x;
            if (p.y < this.min.y) this.min.y = p.y;
            if (p.z < this.min.z) this.min.z = p.z;
            if (p.x > this.max.x) this.max.x = p.x;
            if (p.y > this.max.y) this.max.y = p.y;
            if (p.z > this.max.z) this.max.z = p.z;
        }
        return this;
    }
    expandByScalar(s) {
        this.min.x -= s; this.min.y -= s; this.min.z -= s;
        this.max.x += s; this.max.y += s; this.max.z += s;
        return this;
    }
}

module.exports = { Vector3, Quaternion, Box3 };
```

- [ ] **Step 3: Write a smoke test that verifies the test runner and mock load**

Create `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`:

```javascript
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const THREE = require('./three-mock.js');

test('Vector3 dot product', () => {
    const a = new THREE.Vector3(1, 0, 0);
    const b = new THREE.Vector3(0, 1, 0);
    assert.equal(a.dot(b), 0);
    assert.equal(a.dot(a), 1);
});

test('Quaternion shortest-arc rotates one unit vector onto another', () => {
    const from = new THREE.Vector3(1, 0, 0);
    const to = new THREE.Vector3(0, 1, 0);
    const q = new THREE.Quaternion().setFromUnitVectors(from, to);
    const rotated = from.clone().applyQuaternion(q);
    assert.ok(Math.abs(rotated.x - 0) < 1e-6);
    assert.ok(Math.abs(rotated.y - 1) < 1e-6);
    assert.ok(Math.abs(rotated.z - 0) < 1e-6);
});
```

- [ ] **Step 4: Run the smoke test and verify pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

Expected: `# pass 2 # fail 0`.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/three-mock.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
test(snap): bootstrap node:test runner + minimal THREE mock for snap module

Adds three-mock.js (Vector3 / Quaternion / Box3 — only the operations
snap.js needs) and snap.test.js stub with a smoke test verifying the mock
is correct. Uses Node 18+ built-in node:test, no package.json or npm
dependency added.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Scaffold snap.js public API skeleton

**Why:** Lock the public surface before filling internals so subsequent tasks have a stable contract. Stubs return safe no-op values so callers can exercise the API even before the implementation is complete.

**Files:**
- Create: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

**Reference:** Spec §1.2 (Module shape).

- [ ] **Step 1: Write a failing test for the factory's return shape**

Append to `snap.test.js`:

```javascript
test('createSnapModule returns API with expected methods', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById:  () => null,
        getModelOBB:   () => null,
        getAllModels:  () => [],
        getCameraDir:  () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    assert.equal(typeof api.setConfig,   'function');
    assert.equal(typeof api.getConfig,   'function');
    assert.equal(typeof api.beginDrag,   'function');
    assert.equal(typeof api.updateDrag,  'function');
    assert.equal(typeof api.endDrag,     'function');
    assert.equal(typeof api.getHighlight,'function');
    assert.equal(typeof api.invalidate,  'function');
    const cfg = api.getConfig();
    assert.deepEqual(cfg, { face: true, edge: true, vertex: false, rotation: false });
});

test('updateDrag returns null fields when no drag active', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [],   getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const r = api.updateDrag(new THREE.Vector3());
    assert.equal(r.snapDelta, null);
    assert.equal(r.snapQuat,  null);
    assert.equal(r.highlight, null);
});
```

- [ ] **Step 2: Run tests, verify the new ones FAIL with module-not-found**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

Expected: 2 new tests fail with `Cannot find module './snap.js'`.

- [ ] **Step 3: Create the snap.js skeleton (stubs only, UMD export)**

Create `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`:

```javascript
'use strict';

(function (global, factory) {
    // UMD: CommonJS for node:test, browser global for STL_generate.html
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = factory();
    } else {
        const exported = factory();
        global.createSnapModule = exported.createSnapModule;
    }
}(typeof window !== 'undefined' ? window : globalThis, function () {

    // ── Constants (spec §2.1) ────────────────────────────────────────
    const SNAP_ENTRY_THRESHOLD     = 0.10;
    const SNAP_EXIT_MULT           = 1.5;
    const SNAP_PARALLEL_TOLERANCE  = 0.97;
    const SNAP_SWAP_SCORE_RATIO    = 1.3;
    const CAMERA_FACING_EPSILON    = 0.01;
    const GRID_CELL_SIZE           = 0.30;
    const C_FALLBACK               = 150;
    const SNAP_FACE_DOMINANT_SCORE = 3.8;

    function createSnapModule(deps) {
        const { getModelById, getModelOBB, getAllModels, getCameraDir, THREE } = deps;
        if (!THREE || !THREE.Vector3) throw new Error('snap.js: deps.THREE is required');

        const moduleState = {
            config: { face: true, edge: true, vertex: false, rotation: false },
            active: null,        // SnapActiveState while a drag is in progress
            grid:   null,        // spatial grid built per drag
            dirty:  true         // forces grid rebuild on next beginDrag
        };

        // ── Public API ────────────────────────────────────────────────
        return {
            setConfig(partial) { Object.assign(moduleState.config, partial); },
            getConfig() { return Object.assign({}, moduleState.config); },
            beginDrag(movingIds, opts) {
                moduleState.active = {
                    movingIds: Array.isArray(movingIds) ? movingIds.slice() : [movingIds],
                    rotationGizmoActive: !!(opts && opts.rotationGizmoActive),
                    currentCandidate: null,
                    currentMovingPrim: null,
                    entryDistance: 0,
                    snappedDelta: null,
                    snappedQuat: null
                };
            },
            updateDrag(_pointerDelta) {
                return { snapDelta: null, snapQuat: null, highlight: null };
            },
            endDrag() { moduleState.active = null; },
            getHighlight() {
                return moduleState.active && moduleState.active.currentCandidate
                    ? { kind: moduleState.active.currentCandidate.kind,
                        world: moduleState.active.currentCandidate.world,
                        peerModelId: moduleState.active.currentCandidate.sourceModelId }
                    : null;
            },
            invalidate() { moduleState.dirty = true; }
        };
    }

    return { createSnapModule };
}));
```

- [ ] **Step 4: Run tests and verify all PASS**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): scaffold snap.js with public API skeleton

Adds the createSnapModule factory with all spec §1.2 methods as stubs.
UMD export so the same file works in browser (window.createSnapModule)
and node:test (require). Stubs return safe no-op values so callers can
already wire up the factory.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: TDD — `expandOBBToPrimitives`

**Why:** Spec §2.2 — both moving and peer objects share this expansion. Every downstream filter/score/snap step depends on it.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests for axis-aligned + rotated + degenerate OBBs**

Append to `snap.test.js`:

```javascript
function makeOBB(THREE, center, halfX, halfY, halfZ, axes) {
    return {
        center: new THREE.Vector3(...center),
        half: [halfX, halfY, halfZ],
        axes: axes || [
            new THREE.Vector3(1, 0, 0),
            new THREE.Vector3(0, 1, 0),
            new THREE.Vector3(0, 0, 1)
        ]
    };
}

test('expandOBBToPrimitives: axis-aligned unit cube → 6 faces, 12 edges, 8 verts', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const obb = makeOBB(THREE, [0, 0, 0], 0.5, 0.5, 0.5);
    const prims = api._test_expandOBBToPrimitives(obb);
    assert.equal(prims.faces.length, 6);
    assert.equal(prims.edges.length, 12);
    assert.equal(prims.verts.length, 8);
    // Face normals = ±X, ±Y, ±Z
    const normalSet = new Set(prims.faces.map(f =>
        `${Math.round(f.normal.x)},${Math.round(f.normal.y)},${Math.round(f.normal.z)}`
    ));
    for (const expected of ['1,0,0','-1,0,0','0,1,0','0,-1,0','0,0,1','0,0,-1']) {
        assert.ok(normalSet.has(expected), `missing normal ${expected}`);
    }
    // 8 unique vertices at ±0.5
    for (const v of prims.verts) {
        assert.ok(Math.abs(v.x) === 0.5 && Math.abs(v.y) === 0.5 && Math.abs(v.z) === 0.5);
    }
    // Edge lengths all = 1
    for (const e of prims.edges) {
        assert.ok(Math.abs(e.length - 1.0) < 1e-6, `edge length ${e.length}`);
    }
});

test('expandOBBToPrimitives: rotated 45° around Z → face normals in XY plane', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const s = Math.SQRT1_2;
    const obb = makeOBB(THREE, [0, 0, 0], 0.5, 0.5, 0.5, [
        new THREE.Vector3(s, s, 0), new THREE.Vector3(-s, s, 0), new THREE.Vector3(0, 0, 1)
    ]);
    const prims = api._test_expandOBBToPrimitives(obb);
    // First +X face normal should be (s, s, 0)
    const f0 = prims.faces.find(f => f.normal.x > 0.5 && f.normal.y > 0.5);
    assert.ok(f0, 'expected +X face with rotated normal');
    assert.ok(Math.abs(f0.normal.x - s) < 1e-6);
    assert.ok(Math.abs(f0.normal.y - s) < 1e-6);
});

test('expandOBBToPrimitives: degenerate OBB (zero half-extent) returns empty primitives', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const obb = makeOBB(THREE, [0, 0, 0], 0, 0.5, 0.5);
    const prims = api._test_expandOBBToPrimitives(obb);
    assert.equal(prims.faces.length, 0);
    assert.equal(prims.edges.length, 0);
    assert.equal(prims.verts.length, 0);
});
```

- [ ] **Step 2: Run tests, verify the 3 new tests FAIL with `_test_expandOBBToPrimitives is not a function`**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

- [ ] **Step 3: Implement `expandOBBToPrimitives` and expose via `_test_*`**

In `snap.js`, inside the `createSnapModule` factory, **before** `return {`, add:

```javascript
        function expandOBBToPrimitives(obb) {
            // Spec §2.2 — degenerate guard (any zero-or-near-zero half extent → bail)
            if (!obb || !obb.half || obb.half[0] < 1e-6 || obb.half[1] < 1e-6 || obb.half[2] < 1e-6) {
                return { faces: [], edges: [], verts: [] };
            }
            const { center, axes, half } = obb;
            const verts = [];
            for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
                verts.push(center.clone()
                    .addScaledVector(axes[0], sx * half[0])
                    .addScaledVector(axes[1], sy * half[1])
                    .addScaledVector(axes[2], sz * half[2]));
            }
            // 8-vertex index layout: bit0=sz, bit1=sy, bit2=sx → 000=( -, -, -), 001=( -, -, +), ...
            // Actually our enumeration is sx outer, sy mid, sz inner: index = ((sx==1)*4 + (sy==1)*2 + (sz==1))
            // bottom = sz==-1 → indices 0, 2, 4, 6; top = sz==+1 → 1, 3, 5, 7; verticals: pair sz=-1 with sz=+1
            const edgeIdx = [
                [0, 2], [2, 6], [6, 4], [4, 0],  // bottom (sz=-1)
                [1, 3], [3, 7], [7, 5], [5, 1],  // top    (sz=+1)
                [0, 1], [2, 3], [4, 5], [6, 7]   // verticals
            ];
            const edges = edgeIdx.map(([a, b]) => {
                const p0 = verts[a], p1 = verts[b];
                const dir = p1.clone().sub(p0); const length = dir.length();
                return { p0, p1, dir: length > 0 ? dir.multiplyScalar(1 / length) : dir, length };
            });
            const faces = [];
            for (let i = 0; i < 3; i++) {
                for (const sign of [+1, -1]) {
                    const normal = axes[i].clone().multiplyScalar(sign).normalize();
                    const u = axes[(i + 1) % 3].clone();
                    const v = axes[(i + 2) % 3].clone();
                    const faceCenter = center.clone().addScaledVector(axes[i], sign * half[i]);
                    faces.push({
                        center: faceCenter, normal, u, v,
                        sizeU: 2 * half[(i + 1) % 3], sizeV: 2 * half[(i + 2) % 3]
                    });
                }
            }
            return { faces, edges, verts };
        }
```

In the API return block, add a test-only escape hatch right before `}` of the returned object:

```javascript
            invalidate() { moduleState.dirty = true; },
            // Test-only hooks (consumers should not depend on these)
            _test_expandOBBToPrimitives: expandOBBToPrimitives
```

- [ ] **Step 4: Run tests and verify all PASS**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

Expected: 7 tests pass total.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): expandOBBToPrimitives — 6 faces + 12 edges + 8 vertices in world space

Tested against axis-aligned unit cube, 45°-rotated cube (verifies face
normals in XY plane), and degenerate OBB (zero-extent → empty result, no
crash). Test-only API _test_expandOBBToPrimitives exposed for unit tests.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: TDD — Spatial grid (broad phase)

**Why:** Spec §3 / §4.2 — without spatial pruning, an N-thousand model scene blows the per-frame budget. The grid is a 3D spatial hash with cell size `GRID_CELL_SIZE = 0.30 m`.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests for grid build + query + invalidate**

Append to `snap.test.js`:

```javascript
test('spatial grid: query returns models whose AABB intersects expanded query', () => {
    const { createSnapModule } = require('./snap.js');
    const peers = [
        { id: 'A', aabb: { min: { x: 0, y: 0, z: 0 }, max: { x: 1, y: 1, z: 1 } } },
        { id: 'B', aabb: { min: { x: 5, y: 5, z: 0 }, max: { x: 6, y: 6, z: 1 } } },
        { id: 'C', aabb: { min: { x: 1.05, y: 0, z: 0 }, max: { x: 2, y: 1, z: 1 } } }
    ];
    const api = createSnapModule({
        getModelById: id => peers.find(p => p.id === id) || null,
        getModelOBB:  () => null,
        getAllModels: () => peers,
        getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api._test_buildGrid(peers, ['MOVING']);
    // Query box around (0.5, 0.5, 0.5) with margin 0.20 → covers A and (with expansion) C
    const queryAABB = {
        min: new THREE.Vector3(0.3, 0.3, 0.3),
        max: new THREE.Vector3(0.7, 0.7, 0.7)
    };
    const ids = api._test_queryGrid(queryAABB, 0.20);
    assert.ok(ids.includes('A'), 'A should be in query result');
    assert.ok(ids.includes('C'), 'C should be in query result (within 0.20 margin)');
    assert.ok(!ids.includes('B'), 'B is far away');
});

test('spatial grid: moving ids are excluded from grid', () => {
    const { createSnapModule } = require('./snap.js');
    const peers = [
        { id: 'A', aabb: { min: { x: 0, y: 0, z: 0 }, max: { x: 1, y: 1, z: 1 } } },
        { id: 'M', aabb: { min: { x: 0.1, y: 0.1, z: 0.1 }, max: { x: 0.9, y: 0.9, z: 0.9 } } }
    ];
    const api = createSnapModule({
        getModelById: id => peers.find(p => p.id === id) || null,
        getModelOBB:  () => null,
        getAllModels: () => peers,
        getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api._test_buildGrid(peers, ['M']);
    const queryAABB = {
        min: new THREE.Vector3(0, 0, 0), max: new THREE.Vector3(1, 1, 1)
    };
    const ids = api._test_queryGrid(queryAABB, 0);
    assert.ok(ids.includes('A'));
    assert.ok(!ids.includes('M'), 'moving model should be excluded');
});

test('spatial grid: invalidate() forces rebuild', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api._test_buildGrid([], []);
    assert.ok(api._test_isGridBuilt());
    api.invalidate();
    assert.ok(!api._test_isGridBuilt());
});
```

- [ ] **Step 2: Run tests, verify the 3 new tests FAIL**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

- [ ] **Step 3: Implement spatial grid + AABB helper**

In `snap.js`, after `expandOBBToPrimitives`, add:

```javascript
        function aabbFromOBB(obb) {
            // Conservative world-space AABB from OBB's 8 corners.
            const corners = [];
            const { center, axes, half } = obb;
            for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
                corners.push(center.clone()
                    .addScaledVector(axes[0], sx * half[0])
                    .addScaledVector(axes[1], sy * half[1])
                    .addScaledVector(axes[2], sz * half[2]));
            }
            const min = corners[0].clone(), max = corners[0].clone();
            for (let i = 1; i < corners.length; i++) {
                if (corners[i].x < min.x) min.x = corners[i].x;
                if (corners[i].y < min.y) min.y = corners[i].y;
                if (corners[i].z < min.z) min.z = corners[i].z;
                if (corners[i].x > max.x) max.x = corners[i].x;
                if (corners[i].y > max.y) max.y = corners[i].y;
                if (corners[i].z > max.z) max.z = corners[i].z;
            }
            return { min, max };
        }

        function cellKey(ix, iy, iz) { return ix + ',' + iy + ',' + iz; }
        function cellsTouchedByAABB(aabb, margin) {
            const m = margin || 0;
            const ix0 = Math.floor((aabb.min.x - m) / GRID_CELL_SIZE);
            const iy0 = Math.floor((aabb.min.y - m) / GRID_CELL_SIZE);
            const iz0 = Math.floor((aabb.min.z - m) / GRID_CELL_SIZE);
            const ix1 = Math.floor((aabb.max.x + m) / GRID_CELL_SIZE);
            const iy1 = Math.floor((aabb.max.y + m) / GRID_CELL_SIZE);
            const iz1 = Math.floor((aabb.max.z + m) / GRID_CELL_SIZE);
            const out = [];
            for (let ix = ix0; ix <= ix1; ix++)
                for (let iy = iy0; iy <= iy1; iy++)
                    for (let iz = iz0; iz <= iz1; iz++) out.push(cellKey(ix, iy, iz));
            return out;
        }

        function buildGrid(peers, movingIds) {
            const movingSet = new Set(movingIds || []);
            const grid = new Map();
            const aabbCache = new Map();
            for (const peer of peers) {
                if (movingSet.has(peer.id)) continue;
                // Peer's aabb may be passed directly (test) or come from OBB
                const aabb = peer.aabb && peer.aabb.min ? {
                    min: new THREE.Vector3(peer.aabb.min.x, peer.aabb.min.y, peer.aabb.min.z),
                    max: new THREE.Vector3(peer.aabb.max.x, peer.aabb.max.y, peer.aabb.max.z)
                } : (getModelOBB(peer.id) ? aabbFromOBB(getModelOBB(peer.id)) : null);
                if (!aabb) continue;
                aabbCache.set(peer.id, aabb);
                for (const key of cellsTouchedByAABB(aabb, 0)) {
                    if (!grid.has(key)) grid.set(key, []);
                    grid.get(key).push(peer.id);
                }
            }
            moduleState.grid = { cells: grid, aabbCache, builtAt: Date.now() };
            moduleState.dirty = false;
        }

        function queryGrid(queryAABB, margin) {
            if (!moduleState.grid) return [];
            const ids = new Set();
            for (const key of cellsTouchedByAABB(queryAABB, margin)) {
                const list = moduleState.grid.cells.get(key);
                if (list) for (const id of list) ids.add(id);
            }
            return Array.from(ids);
        }
```

Add to the test-only hooks in the returned object:

```javascript
            _test_expandOBBToPrimitives: expandOBBToPrimitives,
            _test_buildGrid: buildGrid,
            _test_queryGrid: queryGrid,
            _test_isGridBuilt: () => !!moduleState.grid && !moduleState.dirty,
            _test_aabbFromOBB: aabbFromOBB
```

- [ ] **Step 4: Run tests and verify all PASS**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

Expected: 10 tests pass total.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): 3D spatial-hash broad phase (cell size 0.30 m)

Implements buildGrid + queryGrid + aabbFromOBB. Excludes moving model
ids from the grid. Cells are 0.30 m cubes (= entry_threshold × 3) to
guarantee a single query covers any neighbor within snap range. Marked
dirty by invalidate() so the next beginDrag rebuilds.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: TDD — Face filter

**Why:** Spec §2.4 — face is the highest-priority snap kind. Filter rejects: non-parallel pairs, out-of-threshold distance, no-overlap projections, back-facing peers.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests for face filter**

Append to `snap.test.js`:

```javascript
function makePrim(THREE, faces, edges, verts) {
    return { faces: faces || [], edges: edges || [], verts: verts || [] };
}
function makeFace(THREE, cx, cy, cz, nx, ny, nz, sizeU, sizeV) {
    return {
        center: new THREE.Vector3(cx, cy, cz),
        normal: new THREE.Vector3(nx, ny, nz),
        u: new THREE.Vector3(0, 1, 0), v: new THREE.Vector3(0, 0, 1),
        sizeU, sizeV
    };
}

test('filterFaces: parallel + within threshold + overlap → accepted', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(1, 0, 0),
        THREE
    });
    const moving = makePrim(THREE, [makeFace(THREE, 0.05, 0, 0, -1, 0, 0, 1, 1)]);
    const peer   = makePrim(THREE, [makeFace(THREE, 0,    0, 0,  1, 0, 0, 1, 1)]);
    const out = api._test_filterFaces(moving, peer, new THREE.Vector3(1, 0, 0));
    // peer.normal = +X, cameraDir = +X → peer.normal·camera = +1 (back-facing per our convention),
    // so we need cameraDir pointing at the face. Use cameraDir = -X (camera looks toward -X)
    const out2 = api._test_filterFaces(moving, peer, new THREE.Vector3(-1, 0, 0));
    assert.equal(out.length, 0, 'back-face should be filtered (camera dir +X, normal +X)');
    assert.equal(out2.length, 1, 'front-face should pass (camera dir -X)');
});

test('filterFaces: out of threshold → rejected', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const moving = makePrim(THREE, [makeFace(THREE, 0.5, 0, 0, -1, 0, 0, 1, 1)]);  // 0.5m away
    const peer   = makePrim(THREE, [makeFace(THREE, 0,   0, 0,  1, 0, 0, 1, 1)]);
    const out = api._test_filterFaces(moving, peer, new THREE.Vector3(-1, 0, 0));
    assert.equal(out.length, 0, '0.5 m exceeds threshold 0.10');
});

test('filterFaces: not parallel enough → rejected', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const moving = makePrim(THREE, [makeFace(THREE, 0.05, 0, 0, -0.5, 0.866, 0, 1, 1)]);  // 60°
    const peer   = makePrim(THREE, [makeFace(THREE, 0,    0, 0,  1,    0,     0, 1, 1)]);
    const out = api._test_filterFaces(moving, peer, new THREE.Vector3(-1, 0, 0));
    assert.equal(out.length, 0, 'parallelism 0.5 < 0.97');
});

test('filterFaces: no in-plane overlap → rejected', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    // peer face center at origin, sizeU/V = 1 → spans ±0.5 in u/v
    // moving face at (0.05, 5, 0) → 5 units off in u — way outside even with overhang
    const moving = makePrim(THREE, [makeFace(THREE, 0.05, 5, 0, -1, 0, 0, 0.5, 0.5)]);
    const peer   = makePrim(THREE, [makeFace(THREE, 0,    0, 0,  1, 0, 0, 1,   1  )]);
    const out = api._test_filterFaces(moving, peer, new THREE.Vector3(-1, 0, 0));
    assert.equal(out.length, 0, 'no projected overlap');
});
```

- [ ] **Step 2: Run, verify the 4 new tests FAIL with `_test_filterFaces is not a function`**

- [ ] **Step 3: Implement face filter**

In `snap.js`, after the grid helpers, add:

```javascript
        function filterFaces(moving, peer, cameraDir) {
            const out = [];
            for (const pFace of peer.faces) {
                // Camera-facing only: peer face normal must point opposite to camera direction.
                // (CameraDir = direction camera looks; visible if normal·cameraDir < -ε.)
                if (pFace.normal.dot(cameraDir) > -CAMERA_FACING_EPSILON) continue;
                for (const mFace of moving.faces) {
                    const parallel = Math.abs(mFace.normal.dot(pFace.normal));
                    if (parallel < SNAP_PARALLEL_TOLERANCE) continue;
                    // Signed distance of moving face center from peer face plane
                    const ortho = mFace.center.clone().sub(pFace.center);
                    const signedDist = ortho.dot(pFace.normal);
                    const absDist = Math.abs(signedDist);
                    if (absDist > SNAP_ENTRY_THRESHOLD) continue;
                    // In-plane overlap check: project ortho onto u, v
                    const uProj = Math.abs(ortho.dot(pFace.u));
                    const vProj = Math.abs(ortho.dot(pFace.v));
                    const movingRadiusU = Math.max(mFace.sizeU, mFace.sizeV) / 2;
                    if (uProj > pFace.sizeU / 2 + movingRadiusU) continue;
                    if (vProj > pFace.sizeV / 2 + movingRadiusU) continue;
                    const overlap = uProj + vProj;
                    out.push({ kind: 'face', moving: mFace, peer: pFace, signedDist, absDist, overlap, parallel });
                }
            }
            return out;
        }
```

Add `_test_filterFaces: filterFaces` to the test-only hooks.

- [ ] **Step 4: Run tests and verify all PASS**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

Expected: 14 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): face filter (parallelism + threshold + overlap + camera-facing)

Each accepted candidate carries signedDist (for delta computation),
absDist, overlap (for scoring tiebreak), and parallel score. Back-facing
peer faces (normal·cameraDir ≥ -ε) are rejected.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: TDD — Edge filter

**Why:** Spec §2.4 — second-priority snap. Two edges accepted if directionally parallel (within tolerance) AND their shortest 3D segment-segment distance is below the threshold.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests**

Append to `snap.test.js`:

```javascript
function makeEdge(THREE, x0, y0, z0, x1, y1, z1) {
    const p0 = new THREE.Vector3(x0, y0, z0);
    const p1 = new THREE.Vector3(x1, y1, z1);
    const dir = p1.clone().sub(p0); const length = dir.length();
    return { p0, p1, dir: length > 0 ? dir.multiplyScalar(1 / length) : dir, length };
}

test('filterEdges: parallel + within threshold → accepted', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const moving = makePrim(THREE, [], [makeEdge(THREE, 0, 0.03, 0, 1, 0.03, 0)]);
    const peer   = makePrim(THREE, [], [makeEdge(THREE, 0, 0,    0, 1, 0,    0)]);
    const out = api._test_filterEdges(moving, peer);
    assert.equal(out.length, 1);
    assert.ok(out[0].dist < 0.05);
});

test('filterEdges: skew (perpendicular) → rejected', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const moving = makePrim(THREE, [], [makeEdge(THREE, 0, 0, 0, 0, 1, 0)]);
    const peer   = makePrim(THREE, [], [makeEdge(THREE, 0, 0, 0, 1, 0, 0)]);
    const out = api._test_filterEdges(moving, peer);
    assert.equal(out.length, 0);
});

test('filterEdges: parallel but too far → rejected', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const moving = makePrim(THREE, [], [makeEdge(THREE, 0, 0.5, 0, 1, 0.5, 0)]);
    const peer   = makePrim(THREE, [], [makeEdge(THREE, 0, 0,   0, 1, 0,   0)]);
    const out = api._test_filterEdges(moving, peer);
    assert.equal(out.length, 0);
});
```

- [ ] **Step 2: Run, verify 3 new tests FAIL**

- [ ] **Step 3: Implement edge filter (segment-segment distance)**

In `snap.js`, after `filterFaces`, add:

```javascript
        // Closest distance between two 3D line segments. Reference:
        // Real-Time Collision Detection (Ericson) — robust to parallel case.
        function segmentSegmentDistance(s0p0, s0p1, s1p0, s1p1) {
            const d1 = s0p1.clone().sub(s0p0);
            const d2 = s1p1.clone().sub(s1p0);
            const r  = s0p0.clone().sub(s1p0);
            const a = d1.dot(d1), e = d2.dot(d2), f = d2.dot(r);
            let s, t;
            if (a <= 1e-12 && e <= 1e-12) { s = t = 0; }
            else if (a <= 1e-12) { s = 0; t = Math.max(0, Math.min(1, f / e)); }
            else {
                const c = d1.dot(r);
                if (e <= 1e-12) { t = 0; s = Math.max(0, Math.min(1, -c / a)); }
                else {
                    const b = d1.dot(d2);
                    const denom = a * e - b * b;
                    s = denom !== 0 ? Math.max(0, Math.min(1, (b * f - c * e) / denom)) : 0;
                    t = (b * s + f) / e;
                    if (t < 0) { t = 0; s = Math.max(0, Math.min(1, -c / a)); }
                    else if (t > 1) { t = 1; s = Math.max(0, Math.min(1, (b - c) / a)); }
                }
            }
            const c0 = s0p0.clone().addScaledVector(d1, s);
            const c1 = s1p0.clone().addScaledVector(d2, t);
            return { dist: c0.distanceTo(c1), nearestM: c0, nearestP: c1 };
        }

        function filterEdges(moving, peer) {
            const out = [];
            for (const pEdge of peer.edges) {
                for (const mEdge of moving.edges) {
                    const parallel = Math.abs(mEdge.dir.dot(pEdge.dir));
                    if (parallel < SNAP_PARALLEL_TOLERANCE) continue;
                    const r = segmentSegmentDistance(mEdge.p0, mEdge.p1, pEdge.p0, pEdge.p1);
                    if (r.dist > SNAP_ENTRY_THRESHOLD) continue;
                    out.push({ kind: 'edge', moving: mEdge, peer: pEdge,
                               dist: r.dist, nearestM: r.nearestM, nearestP: r.nearestP, parallel });
                }
            }
            return out;
        }
```

Add `_test_filterEdges: filterEdges` to test-only hooks.

- [ ] **Step 4: Run tests, verify all PASS**

Expected: 17 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): edge filter (parallelism + segment-segment distance)

Closest segment-segment distance via clamped Lagrange method (Ericson §5.1.9).
Rejects skew or far-apart edges. Accepted candidates carry the closest-point
pair on each segment for later snapDelta computation.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: TDD — Vertex filter

**Why:** Spec §2.4 — third-priority snap, plain Euclidean distance check.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests**

Append to `snap.test.js`:

```javascript
test('filterVertices: within threshold → accepted; outside → rejected', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const moving = makePrim(THREE, [], [], [new THREE.Vector3(0.02, 0, 0), new THREE.Vector3(0.5, 0, 0)]);
    const peer   = makePrim(THREE, [], [], [new THREE.Vector3(0,    0, 0), new THREE.Vector3(1,   0, 0)]);
    const out = api._test_filterVertices(moving, peer);
    // Pair (0.02,0,0) ↔ (0,0,0) at 0.02 → accepted; the rest are too far
    assert.equal(out.length, 1);
    assert.ok(out[0].dist < 0.05);
});
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

In `snap.js`, after `filterEdges`, add:

```javascript
        function filterVertices(moving, peer) {
            const out = [];
            for (const pV of peer.verts) {
                for (const mV of moving.verts) {
                    const dist = mV.distanceTo(pV);
                    if (dist > SNAP_ENTRY_THRESHOLD) continue;
                    out.push({ kind: 'vertex', moving: mV, peer: pV, dist });
                }
            }
            return out;
        }
```

Add `_test_filterVertices: filterVertices` to hooks.

- [ ] **Step 4: Run, verify PASS** (18 tests)

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): vertex filter (Euclidean distance < SNAP_ENTRY_THRESHOLD)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: TDD — Scoring + pickBest with hysteresis

**Why:** Spec §2.4 + §2.5 — combine candidates from F/E/V into a single ranked pool, apply baseWeight + distance + overlapBonus, then route through the hysteresis state machine: stickiness on swap (need score×1.3), release on exit_radius (entry × 1.5).

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests for scoring + state machine**

Append to `snap.test.js`:

```javascript
test('scoreCandidate: face base weight 3.0 + distance bonus', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const c = { kind: 'face', absDist: 0.05, overlap: 0 };
    const s = api._test_scoreCandidate(c);
    // base 3.0 + (1 - 0.05/0.10) = 3.5, plus 0.5 × 1 (no overlap penalty)
    assert.ok(s >= 3.4 && s <= 4.1, `expected ~3.5-4.0, got ${s}`);
});

test('pickBest: hysteresis — small jitter keeps current candidate', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const cA = { kind: 'face', absDist: 0.05, overlap: 0, peer: 'A', moving: 'M0' };
    const cB = { kind: 'face', absDist: 0.04, overlap: 0, peer: 'B', moving: 'M0' };
    api._test_setHysteresisCurrent(cA);
    // cB is slightly closer but not 1.3× better → keep cA
    const pick = api._test_pickBest({ faces: [cA, cB], edges: [], verts: [] });
    assert.equal(pick.peer, 'A', 'should stick to A despite B being closer');
});

test('pickBest: switch when new candidate ≥ 1.3× current score', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const cA = { kind: 'vertex', dist: 0.09, peer: 'A', moving: 'M0' };  // base 1.0 + 0.1 = 1.1
    const cB = { kind: 'face',   absDist: 0.01, overlap: 0, peer: 'B', moving: 'M0' };  // base 3.0 + 0.9 = 3.9
    api._test_setHysteresisCurrent(cA);
    const pick = api._test_pickBest({ faces: [cB], edges: [], verts: [cA] });
    assert.equal(pick.peer, 'B', 'face B vastly outscores vertex A → swap');
});

test('pickBest: respects config — skip face/edge/vertex if disabled', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.setConfig({ face: false, edge: true, vertex: false, rotation: false });
    const cFace = { kind: 'face', absDist: 0.01, overlap: 0, peer: 'F' };
    const cEdge = { kind: 'edge', dist: 0.05,             peer: 'E' };
    const pick = api._test_pickBest({ faces: [cFace], edges: [cEdge], verts: [] });
    assert.equal(pick.peer, 'E', 'face disabled → must pick edge');
});
```

- [ ] **Step 2: Run, verify 4 new tests FAIL**

- [ ] **Step 3: Implement scoring + pickBest**

In `snap.js`, after `filterVertices`, add:

```javascript
        function scoreCandidate(c) {
            const base = c.kind === 'face' ? 3.0 : c.kind === 'edge' ? 2.0 : 1.0;
            const dist = c.kind === 'face' ? c.absDist : c.dist;
            const distScore = Math.max(0, 1 - (dist / SNAP_ENTRY_THRESHOLD));
            // Face overlap bonus: smaller projected offset = better overlap
            const overlapBonus = c.kind === 'face' ? Math.max(0, 1 - (c.overlap || 0)) : 0;
            return base + distScore + overlapBonus * 0.5;
        }

        function pickBest(filtered) {
            const cfg = moduleState.config;
            const pool = [];
            if (cfg.face)   for (const c of filtered.faces) pool.push(Object.assign({ score: scoreCandidate(c) }, c));
            if (cfg.edge)   for (const c of filtered.edges) pool.push(Object.assign({ score: scoreCandidate(c) }, c));
            if (cfg.vertex) for (const c of filtered.verts) pool.push(Object.assign({ score: scoreCandidate(c) }, c));
            if (pool.length === 0) return null;
            pool.sort((a, b) => b.score - a.score);
            const top = pool[0];

            // Early termination: dominant face skips edge/vertex evaluation in caller (caller checks)
            const cur = moduleState.active && moduleState.active.currentCandidate;
            if (cur) {
                const sameId = top.peer === cur.peer && top.moving === cur.moving && top.kind === cur.kind;
                if (!sameId && top.score < (cur.score || scoreCandidate(cur)) * SNAP_SWAP_SCORE_RATIO) {
                    // Stick with current — re-emit current with refreshed score
                    return Object.assign({}, cur, { score: scoreCandidate(cur) });
                }
            }
            return top;
        }
```

Add to test-only hooks:

```javascript
            _test_scoreCandidate: scoreCandidate,
            _test_pickBest: pickBest,
            _test_setHysteresisCurrent: (cand) => {
                if (!moduleState.active) {
                    moduleState.active = { movingIds: ['_test_'], currentCandidate: null };
                }
                moduleState.active.currentCandidate = cand;
            }
```

- [ ] **Step 4: Run, verify all PASS** (22 tests)

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): scoring + pickBest with hysteresis stickiness

Scoring: base weight (face=3, edge=2, vertex=1) + distance falloff +
face overlap bonus. pickBest applies the candidate-swap hysteresis:
new candidate must score > old × 1.3 to dethrone the current target,
preventing oscillation between near-equal neighbors.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: TDD — `computeSnapDelta` (one-shot correction vector)

**Why:** Spec §2.6 — translates the moving primitive onto the peer primitive. Returned vector is applied ONCE per frame on top of `anchorStart + pointerDelta` (caller's responsibility), never accumulated.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests for face / edge / vertex delta**

Append to `snap.test.js`:

```javascript
test('computeSnapDelta: face → translation along peer normal makes faces coplanar', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const mFace = makeFace(THREE, 0.05, 0, 0, -1, 0, 0, 1, 1);
    const pFace = makeFace(THREE, 0,    0, 0,  1, 0, 0, 1, 1);
    const cand = { kind: 'face', moving: mFace, peer: pFace, signedDist: 0.05, absDist: 0.05 };
    const delta = api._test_computeSnapDelta(cand);
    // moving center at (0.05, 0, 0); peer plane through origin, normal +X
    // signedDist = (mCenter - pCenter) · pNormal = (0.05, 0, 0) · (1,0,0) = 0.05
    // Need to translate moving by -signedDist along pNormal → delta = (-0.05, 0, 0)
    assert.ok(Math.abs(delta.x - (-0.05)) < 1e-6, `dx=${delta.x}`);
    assert.ok(Math.abs(delta.y) < 1e-6);
    assert.ok(Math.abs(delta.z) < 1e-6);
});

test('computeSnapDelta: edge → perpendicular translation to peer line', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const cand = {
        kind: 'edge',
        moving: makeEdge(THREE, 0, 0.03, 0, 1, 0.03, 0),
        peer:   makeEdge(THREE, 0, 0,    0, 1, 0,    0),
        nearestM: new THREE.Vector3(0.5, 0.03, 0),
        nearestP: new THREE.Vector3(0.5, 0,    0)
    };
    const delta = api._test_computeSnapDelta(cand);
    // Move moving down by 0.03 to coincide with peer
    assert.ok(Math.abs(delta.y - (-0.03)) < 1e-6, `dy=${delta.y}`);
});

test('computeSnapDelta: vertex → straight translation', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    const cand = {
        kind: 'vertex',
        moving: new THREE.Vector3(0.02, 0.01, 0),
        peer:   new THREE.Vector3(0,    0,    0)
    };
    const delta = api._test_computeSnapDelta(cand);
    assert.ok(Math.abs(delta.x - (-0.02)) < 1e-6);
    assert.ok(Math.abs(delta.y - (-0.01)) < 1e-6);
    assert.ok(Math.abs(delta.z) < 1e-6);
});
```

- [ ] **Step 2: Run, verify 3 new tests FAIL**

- [ ] **Step 3: Implement**

In `snap.js`, after `pickBest`, add:

```javascript
        function computeSnapDelta(cand) {
            if (!cand) return null;
            if (cand.kind === 'face') {
                // Move moving center by -signedDist along peer normal
                return cand.peer.normal.clone().multiplyScalar(-cand.signedDist);
            }
            if (cand.kind === 'edge') {
                // Translate so moving's nearest point coincides with peer's nearest point
                return cand.nearestP.clone().sub(cand.nearestM);
            }
            if (cand.kind === 'vertex') {
                return cand.peer.clone().sub(cand.moving);
            }
            return null;
        }
```

Add `_test_computeSnapDelta: computeSnapDelta` to hooks.

- [ ] **Step 4: Run, verify PASS** (25 tests)

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): computeSnapDelta — one-shot correction vector for face/edge/vertex

Critical anti-jitter design: the returned delta is applied ONCE per frame
by the caller (on top of anchorStart + pointerDelta), never accumulated
across frames. Face uses signed-distance projection along peer normal;
edge uses nearest-point pairing; vertex is a straight translation.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: TDD — `computeRotationSnapQuat` (4-condition gate)

**Why:** Spec §2.7 — only fires when all four conditions are met. Returns shortest-arc quaternion that aligns the moving face normal onto the peer face normal.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing tests for the four conditions**

Append to `snap.test.js`:

```javascript
test('rotationSnap: all four conditions met → returns quaternion aligning normals', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.setConfig({ face: true, edge: true, vertex: false, rotation: true });
    api.beginDrag(['m1'], { rotationGizmoActive: true });
    // Moving normal at 25° off peer normal — parallelism = cos(25°) ≈ 0.91, in [0.85, 0.97)
    const mFace = makeFace(THREE, 0, 0, 0, Math.cos(25 * Math.PI / 180), Math.sin(25 * Math.PI / 180), 0, 1, 1);
    const pFace = makeFace(THREE, 0, 0, 0, 1, 0, 0, 1, 1);
    const cand = { kind: 'face', moving: mFace, peer: pFace, parallel: Math.abs(mFace.normal.dot(pFace.normal)) };
    const quat = api._test_computeRotationSnapQuat(cand);
    assert.ok(quat !== null, 'expected non-null quaternion');
    // Rotate moving normal by quat → should equal peer normal
    const rotated = mFace.normal.clone().applyQuaternion(quat);
    assert.ok(Math.abs(rotated.x - 1) < 1e-3 && Math.abs(rotated.y) < 1e-3, `rotated normal ${rotated.x},${rotated.y},${rotated.z}`);
});

test('rotationSnap: rotation toggle off → null', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.setConfig({ face: true, edge: true, vertex: false, rotation: false });
    api.beginDrag(['m1'], { rotationGizmoActive: true });
    const cand = { kind: 'face',
        moving: makeFace(THREE, 0, 0, 0, 0.9, 0.4, 0, 1, 1),
        peer:   makeFace(THREE, 0, 0, 0, 1,   0,   0, 1, 1),
        parallel: 0.9
    };
    assert.equal(api._test_computeRotationSnapQuat(cand), null);
});

test('rotationSnap: rotation gizmo not active → null', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.setConfig({ face: true, edge: true, vertex: false, rotation: true });
    api.beginDrag(['m1'], { rotationGizmoActive: false });
    const cand = { kind: 'face',
        moving: makeFace(THREE, 0, 0, 0, 0.9, 0.4, 0, 1, 1),
        peer:   makeFace(THREE, 0, 0, 0, 1,   0,   0, 1, 1),
        parallel: 0.9
    };
    assert.equal(api._test_computeRotationSnapQuat(cand), null);
});

test('rotationSnap: face toggle off → null (rotation needs face as reference)', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.setConfig({ face: false, edge: true, vertex: false, rotation: true });
    api.beginDrag(['m1'], { rotationGizmoActive: true });
    const cand = { kind: 'face',
        moving: makeFace(THREE, 0, 0, 0, 0.9, 0.4, 0, 1, 1),
        peer:   makeFace(THREE, 0, 0, 0, 1,   0,   0, 1, 1),
        parallel: 0.9
    };
    assert.equal(api._test_computeRotationSnapQuat(cand), null);
});

test('rotationSnap: already parallel (>0.97) → null (no correction needed)', () => {
    const { createSnapModule } = require('./snap.js');
    const api = createSnapModule({
        getModelById: () => null, getModelOBB: () => null,
        getAllModels: () => [], getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.setConfig({ face: true, edge: true, vertex: false, rotation: true });
    api.beginDrag(['m1'], { rotationGizmoActive: true });
    const cand = { kind: 'face',
        moving: makeFace(THREE, 0, 0, 0, 0.999, 0.05, 0, 1, 1),
        peer:   makeFace(THREE, 0, 0, 0, 1,     0,    0, 1, 1),
        parallel: 0.999
    };
    assert.equal(api._test_computeRotationSnapQuat(cand), null, 'parallel ≥ 0.97 → no rotation correction');
});
```

- [ ] **Step 2: Run, verify 5 new tests FAIL**

- [ ] **Step 3: Implement**

In `snap.js`, after `computeSnapDelta`, add:

```javascript
        function computeRotationSnapQuat(cand) {
            const cfg = moduleState.config;
            const act = moduleState.active;
            // Four-condition gate (spec §2.7)
            if (!cfg.rotation) return null;
            if (!act || !act.rotationGizmoActive) return null;
            if (!cfg.face) return null;
            if (!cand || cand.kind !== 'face') return null;
            const par = cand.parallel != null ? cand.parallel : Math.abs(cand.moving.normal.dot(cand.peer.normal));
            if (par >= SNAP_PARALLEL_TOLERANCE) return null;     // already aligned
            if (par <  0.85)                    return null;     // too far off — assume user intent differs
            // Shortest-arc quaternion from moving normal onto peer normal.
            // Both should be unit vectors; clone to avoid mutating.
            const mN = cand.moving.normal.clone().normalize();
            const pN = cand.peer.normal.clone().normalize();
            // If moving and peer normals point in same hemisphere, rotate to align;
            // otherwise we want to align the BACK of moving's face to peer (flip mN).
            const aligned = mN.dot(pN) >= 0 ? mN : mN.clone().multiplyScalar(-1);
            return new THREE.Quaternion().setFromUnitVectors(aligned, pN);
        }
```

Add `_test_computeRotationSnapQuat: computeRotationSnapQuat` to hooks.

- [ ] **Step 4: Run, verify all PASS** (30 tests)

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): rotation-snap quaternion under four-condition gate

Returns null unless: rotation toggle ON + gizmo active + face toggle ON +
candidate parallelism in [0.85, 0.97). Uses shortest-arc quaternion to
align moving face normal onto peer face normal; flips moving normal if
they point in opposite hemispheres so the face rotates the short way.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: TDD — Wire `updateDrag` end-to-end (broad → narrow → pick → delta+quat, with fallback)

**Why:** Spec §2 + §4.5 — replace the stub `updateDrag` with the actual pipeline. Includes degraded-mode fallback when the broad-phase neighbor count exceeds `C_FALLBACK = 150`.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write integration test using a tiny fake scene**

Append to `snap.test.js`:

```javascript
function makeOBBModel(THREE, id, cx, cy, cz, hx, hy, hz, axes) {
    return {
        id,
        obb: {
            center: new THREE.Vector3(cx, cy, cz),
            half: [hx, hy, hz],
            axes: axes || [
                new THREE.Vector3(1, 0, 0),
                new THREE.Vector3(0, 1, 0),
                new THREE.Vector3(0, 0, 1)
            ]
        }
    };
}

test('updateDrag end-to-end: moving cube near peer cube → returns face snapDelta', () => {
    const { createSnapModule } = require('./snap.js');
    // Two unit cubes, peer at origin, moving 0.05 m to the +X side
    const moving = makeOBBModel(THREE, 'M', 1.05, 0, 0, 0.5, 0.5, 0.5);
    const peer   = makeOBBModel(THREE, 'P', 0,    0, 0, 0.5, 0.5, 0.5);
    const models = [moving, peer];
    const api = createSnapModule({
        getModelById: id => models.find(m => m.id === id) || null,
        getModelOBB:  id => (models.find(m => m.id === id) || {}).obb || null,
        getAllModels: () => models,
        getCameraDir: () => new THREE.Vector3(-1, 0, 0),
        THREE
    });
    api.beginDrag(['M'], { rotationGizmoActive: false });
    const r = api.updateDrag(new THREE.Vector3());
    assert.ok(r.snapDelta, 'expected snapDelta');
    // moving's -X face center at (0.55, 0, 0); peer's +X face at (0.5, 0, 0); signedDist = 0.05
    // delta = -signedDist along peer normal +X = (-0.05, 0, 0)
    assert.ok(Math.abs(r.snapDelta.x - (-0.05)) < 1e-6, `dx=${r.snapDelta.x}`);
    assert.ok(r.highlight, 'expected highlight info');
    assert.equal(r.highlight.kind, 'face');
});

test('updateDrag fallback mode: > C_FALLBACK neighbors → face-only path, no crash', () => {
    const { createSnapModule } = require('./snap.js');
    // 200 small peers around the moving cube
    const models = [makeOBBModel(THREE, 'M', 0, 0, 0, 0.05, 0.05, 0.05)];
    for (let i = 0; i < 200; i++) {
        const ang = (i / 200) * 2 * Math.PI;
        models.push(makeOBBModel(THREE, 'P' + i,
            Math.cos(ang) * 0.05, Math.sin(ang) * 0.05, 0, 0.04, 0.04, 0.04));
    }
    const api = createSnapModule({
        getModelById: id => models.find(m => m.id === id) || null,
        getModelOBB:  id => (models.find(m => m.id === id) || {}).obb || null,
        getAllModels: () => models,
        getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.beginDrag(['M'], { rotationGizmoActive: false });
    const r = api.updateDrag(new THREE.Vector3());
    // No assertion on candidates (depends on geometry), just verify no crash + degraded flag set
    assert.ok(r.highlight === null || r.highlight.kind === 'face',
        'fallback should produce only face snaps');
});

test('updateDrag: empty scene → null fields', () => {
    const { createSnapModule } = require('./snap.js');
    const models = [makeOBBModel(THREE, 'M', 0, 0, 0, 0.5, 0.5, 0.5)];
    const api = createSnapModule({
        getModelById: id => models.find(m => m.id === id) || null,
        getModelOBB:  id => (models.find(m => m.id === id) || {}).obb || null,
        getAllModels: () => models,
        getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE
    });
    api.beginDrag(['M'], { rotationGizmoActive: false });
    const r = api.updateDrag(new THREE.Vector3());
    assert.equal(r.snapDelta, null);
    assert.equal(r.snapQuat, null);
    assert.equal(r.highlight, null);
});
```

- [ ] **Step 2: Run, verify the 3 new tests FAIL**

- [ ] **Step 3: Replace stub `updateDrag` and `beginDrag` with the real pipeline**

In `snap.js`, replace the existing `beginDrag` and `updateDrag` methods inside the returned object:

```javascript
            beginDrag(movingIds, opts) {
                const ids = Array.isArray(movingIds) ? movingIds.slice() : [movingIds];
                moduleState.active = {
                    movingIds: ids,
                    rotationGizmoActive: !!(opts && opts.rotationGizmoActive),
                    currentCandidate: null,
                    currentMovingPrim: null,
                    entryDistance: 0,
                    snappedDelta: null,
                    snappedQuat: null,
                    peerPrimsCache: new Map()           // peer id → expanded primitives
                };
                if (moduleState.dirty || !moduleState.grid) {
                    buildGrid(getAllModels(), ids);
                }
            },

            updateDrag(_pointerDelta) {
                const act = moduleState.active;
                if (!act) return { snapDelta: null, snapQuat: null, highlight: null };
                const movingId = act.movingIds[0];
                const movingObb = getModelOBB(movingId);
                if (!movingObb) {
                    act.currentCandidate = null;
                    return { snapDelta: null, snapQuat: null, highlight: null };
                }
                const movingAabb = aabbFromOBB(movingObb);
                const margin = SNAP_ENTRY_THRESHOLD * SNAP_EXIT_MULT;
                const candidateIds = queryGrid(movingAabb, margin);
                const cameraDir = getCameraDir();

                // Degraded mode if too many candidates (spec §4.5)
                const degraded = candidateIds.length > C_FALLBACK;

                const movingPrims = expandOBBToPrimitives(movingObb);
                if (movingPrims.faces.length === 0) {
                    act.currentCandidate = null;
                    return { snapDelta: null, snapQuat: null, highlight: null };
                }

                const filtered = { faces: [], edges: [], verts: [] };
                for (const peerId of candidateIds) {
                    let peerPrims = act.peerPrimsCache.get(peerId);
                    if (!peerPrims) {
                        const obb = getModelOBB(peerId);
                        if (!obb) continue;
                        if (degraded) {
                            // Build only the 6 face centers for fallback
                            const fullPrims = expandOBBToPrimitives(obb);
                            peerPrims = { faces: fullPrims.faces, edges: [], verts: [] };
                        } else {
                            peerPrims = expandOBBToPrimitives(obb);
                        }
                        act.peerPrimsCache.set(peerId, peerPrims);
                    }
                    const movingForPeer = degraded
                        ? { faces: movingPrims.faces, edges: [], verts: [] }
                        : movingPrims;
                    filtered.faces.push(...filterFaces(movingForPeer, peerPrims, cameraDir));
                    if (!degraded) {
                        filtered.edges.push(...filterEdges(movingForPeer, peerPrims));
                        filtered.verts.push(...filterVertices(movingForPeer, peerPrims));
                    }
                }

                // Hysteresis exit check: if we have a current candidate, see if its
                // current distance still falls within EXIT radius. If not, drop it.
                if (act.currentCandidate) {
                    const cur = act.currentCandidate;
                    let curDist;
                    if (cur.kind === 'face') {
                        curDist = Math.abs(cur.moving.center.clone().sub(cur.peer.center).dot(cur.peer.normal));
                    } else if (cur.kind === 'edge') {
                        curDist = cur.moving.p0.distanceTo(cur.peer.p0); // approx — pick best segment dist
                    } else {
                        curDist = cur.moving.distanceTo(cur.peer);
                    }
                    if (curDist > SNAP_ENTRY_THRESHOLD * SNAP_EXIT_MULT) {
                        act.currentCandidate = null;
                    }
                }

                const best = pickBest(filtered);
                act.currentCandidate = best;

                const snapDelta = computeSnapDelta(best);
                const snapQuat  = computeRotationSnapQuat(best);

                let highlight = null;
                if (best) {
                    let world;
                    if (best.kind === 'face') {
                        world = { center: best.peer.center.clone(),
                                  normal: best.peer.normal.clone(),
                                  u: best.peer.u.clone(), v: best.peer.v.clone(),
                                  sizeU: best.peer.sizeU, sizeV: best.peer.sizeV };
                    } else if (best.kind === 'edge') {
                        world = { p0: best.peer.p0.clone(), p1: best.peer.p1.clone() };
                    } else {
                        world = { point: best.peer.clone() };
                    }
                    highlight = { kind: best.kind, world, peerModelId: best.peerModelId || null,
                                  degraded };
                }

                return { snapDelta, snapQuat, highlight };
            },
```

Note: the existing `endDrag` is fine. `getHighlight` already reads from `moduleState.active.currentCandidate` and now also benefits.

**Important** — to track `peerModelId` on each candidate so highlights can locate the source model, update the three filter signatures to take a final optional `peerId` parameter and tag every accepted candidate with `peerModelId: peerId`.

The signature change is backwards-compatible (Tasks 5-7 tests pass `undefined` for peerId, which becomes `peerModelId: undefined` — that field is simply absent in the highlight). Concretely:

```javascript
function filterFaces(moving, peer, cameraDir, peerId) {
    // ... existing body ... but inside the `out.push(...)` call, change:
    //   { kind: 'face', moving: mFace, peer: pFace, signedDist, absDist, overlap, parallel }
    // to:
    //   { kind: 'face', moving: mFace, peer: pFace, signedDist, absDist, overlap, parallel, peerModelId: peerId }
}

function filterEdges(moving, peer, peerId) {
    // Inside the out.push(...): add peerModelId: peerId
}

function filterVertices(moving, peer, peerId) {
    // Inside the out.push(...): add peerModelId: peerId
}
```

In the new `updateDrag` body shown above, the calls become:
```javascript
filtered.faces.push(...filterFaces(movingForPeer, peerPrims, cameraDir, peerId));
if (!degraded) {
    filtered.edges.push(...filterEdges(movingForPeer, peerPrims, peerId));
    filtered.verts.push(...filterVertices(movingForPeer, peerPrims, peerId));
}
```

- [ ] **Step 4: Run, verify all PASS** (33 tests)

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js
node --test snap.test.js
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): wire updateDrag end-to-end with broad/narrow phase + degraded mode

beginDrag builds the spatial grid + caches peer primitives. updateDrag
runs broad phase → narrow filter → pickBest hysteresis → snapDelta +
snapQuat. Switches to face-only fallback when neighbor count > C_FALLBACK
(150). Highlight payload includes peerModelId for the visual overlay
renderer.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Wire snap.js into `STL_generate.html` (load + factory init + invalidate hooks)

**Why:** Get the new module loaded and instantiated in the browser without yet using it. This isolates load-order issues from behavior changes.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add the script tag**

Find the `<script>` blocks where `three.min.js` is loaded (around the top of the file). After `OrbitControls.js` / `STLLoader.js` and BEFORE the main inline `<script>` (the one with the IIFE), insert:

```html
<script src="js/snap.js"></script>
```

Verify file layout: `web/js/snap.js` is reachable from `STL_generate.html` via relative path `js/snap.js`.

- [ ] **Step 2: Initialize the snap module inside the main IIFE, after camera/scene exist**

Search for the line where the camera is created (e.g. `camera = new THREE.PerspectiveCamera(...)`) inside the main IIFE. After `state` is fully populated (after the `state = { ... }` assignment block), add:

```javascript
    // ── Snap module (snap.js) ────────────────────────────────────
    const snapModule = window.createSnapModule({
        getModelById: getModelById,
        getModelOBB:  getModelOBB,
        getAllModels: () => state.models,
        getCameraDir: () => {
            const v = new THREE.Vector3();
            camera.getWorldDirection(v);
            return v;
        },
        THREE: THREE
    });
    window.__snapModule = snapModule;  // expose for headless integration tests
```

- [ ] **Step 3: Wire `invalidate()` to existing model add/delete/transform/undo paths**

Search for these existing functions/handlers and add `snapModule.invalidate();` at the END of each (just before they return):

- The function that adds a model (search for `state.models.push(`)
- The function that deletes a model (search for `state.models.splice(` or `state.models = state.models.filter(`)
- `syncModelRenderTransform(item)` — just before its closing brace
- `undo()` and `redo()` — just before their closing braces

Use Grep to find each, then add the single line. Example:

```javascript
function syncModelRenderTransform(item) {
    // ... existing body ...
    snapModule.invalidate();
}
```

- [ ] **Step 4: Manually verify the script loads in the browser without error**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/server.py --port 8005 --ws-port 8006 --case-dir case/Tri_angle &
sleep 1
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>&1 | tail -1
browser-use open http://localhost:8005/login 2>&1 | tail -1
# (login + navigate to STL_generate.html — see prior session for credentials)
browser-use eval "typeof window.__snapModule + ' / config: ' + JSON.stringify(window.__snapModule.getConfig())"
```

Expected: `object / config: {"face":true,"edge":true,"vertex":false,"rotation":false}`

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(snap): load snap.js + initialize createSnapModule with host deps

Adds <script src="js/snap.js"> and creates a single snapModule instance
inside the main IIFE. Wires invalidate() to model add/delete,
syncModelRenderTransform, and undo/redo so the spatial grid stays fresh
across non-drag mutations. Exposed as window.__snapModule for headless
integration tests. Snap behavior is NOT yet active — wired in next task.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Replace `computeProximitySnap` call sites with snapModule.updateDrag

**Why:** Cut over the actual drag behavior. Two call sites: 3D viewport drag (~L15368) and a secondary site (~L16430). Each needs:
1. `snapModule.beginDrag(movingIds, { rotationGizmoActive })` at drag start
2. `snapModule.updateDrag(...)` per frame (replacing `computeProximitySnap`)
3. `snapModule.endDrag()` at drag end
4. Apply `snapDelta` and `snapQuat` from the result

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Find drag-start, drag-update, drag-end functions for modelMove**

Use Grep to confirm the locations:

```
Grep: pattern: "beginModelMoveDrag", path: STL_generate.html → line ~15244
Grep: pattern: "computeProximitySnap\(", path: STL_generate.html → lines 15368, 16430
Grep: pattern: "endPatchDrag", path: STL_generate.html → ~15706 (handles modelMove end)
```

- [ ] **Step 2: Insert `snapModule.beginDrag` at drag-start**

Inside `beginModelMoveDrag` (~L15244), at the END of the function (just before the trailing `}` and `return`), add:

```javascript
            // Snap module: begin a drag session
            snapModule.beginDrag(dragState.modelIds || [dragState.modelId], {
                rotationGizmoActive: false  // model-move drag, not gizmo drag
            });
```

- [ ] **Step 3: Replace the snap apply block at L15367-L15377**

Locate this block (currently between line 15367 and 15377):

```javascript
            // Apply snap
            if (state.snapFace || state.snapEdge || state.snapGrid) {
                const snap = state.snapGrid ? computeGridSnap(movingIds) : computeProximitySnap(movingIds, state.snapFace, state.snapEdge);
                if ((dragState.axes.x && Math.abs(snap.x) > 1e-8) || (dragState.axes.y && Math.abs(snap.y) > 1e-8) || (dragState.axes.z && Math.abs(snap.z) > 1e-8)) {
                    movingModels.forEach(item => {
                        if (dragState.axes.x) item.anchor.x += snap.x;
                        if (dragState.axes.y) item.anchor.y += snap.y;
                        if (dragState.axes.z) item.anchor.z += snap.z;
                        syncModelRenderTransform(item);
                    });
                }
            }
```

Replace with:

```javascript
            // Apply snap. Grid snap is orthogonal (own toggle); face/edge/vertex/rotation
            // come from the new snap module which returns a one-shot delta + quat.
            if (state.snapGrid) {
                const snap = computeGridSnap(movingIds);
                if ((dragState.axes.x && Math.abs(snap.x) > 1e-8) || (dragState.axes.y && Math.abs(snap.y) > 1e-8) || (dragState.axes.z && Math.abs(snap.z) > 1e-8)) {
                    movingModels.forEach(item => {
                        if (dragState.axes.x) item.anchor.x += snap.x;
                        if (dragState.axes.y) item.anchor.y += snap.y;
                        if (dragState.axes.z) item.anchor.z += snap.z;
                        syncModelRenderTransform(item);
                    });
                }
            } else {
                const result = snapModule.updateDrag(new THREE.Vector3(deltaLocal.x, deltaLocal.y, deltaLocal.z));
                if (result.snapDelta) {
                    movingModels.forEach(item => {
                        if (dragState.axes.x) item.anchor.x += result.snapDelta.x;
                        if (dragState.axes.y) item.anchor.y += result.snapDelta.y;
                        if (dragState.axes.z) item.anchor.z += result.snapDelta.z;
                        syncModelRenderTransform(item);
                    });
                }
                if (result.snapQuat) {
                    movingModels.forEach(item => {
                        item.transform.quaternion = result.snapQuat.toArray();
                        syncModelRenderTransform(item);
                    });
                }
            }
```

- [ ] **Step 4: Repeat the same replacement at the second call site (~L16430)**

```bash
grep -n "computeProximitySnap" /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: ONE remaining occurrence (around L16427-L16432, inside another drag handler such as a touch path or a secondary update loop). Read 10 lines of context around it. The shape is:

```javascript
if (state.snapFace || state.snapEdge || state.snapGrid) {
    const snap = state.snapGrid
        ? computeGridSnap(movingIds)
        : computeProximitySnap(movingIds, state.snapFace, state.snapEdge);
    // ... applies snap.x/y/z to anchors, possibly without per-axis gating ...
}
```

Replace with the same two-branch structure used in Step 3, scaled to the local variable names. Concretely:

```javascript
if (state.snapGrid) {
    const snap = computeGridSnap(movingIds);
    if (Math.abs(snap.x) > 1e-8 || Math.abs(snap.y) > 1e-8 || Math.abs(snap.z) > 1e-8) {
        movingModels.forEach(item => {
            item.anchor.x += snap.x;
            item.anchor.y += snap.y;
            item.anchor.z += snap.z;
            syncModelRenderTransform(item);
        });
    }
} else {
    const result = snapModule.updateDrag(new THREE.Vector3());
    if (result.snapDelta) {
        movingModels.forEach(item => {
            item.anchor.x += result.snapDelta.x;
            item.anchor.y += result.snapDelta.y;
            item.anchor.z += result.snapDelta.z;
            syncModelRenderTransform(item);
        });
    }
    if (result.snapQuat) {
        movingModels.forEach(item => {
            item.transform.quaternion = result.snapQuat.toArray();
            syncModelRenderTransform(item);
        });
    }
}
```

If the surrounding code references `dragState.axes.x/y/z` per-axis gating (mirroring Step 3), keep those gates around each `item.anchor.X += ...` line.

- [ ] **Step 5: Insert `snapModule.endDrag()` at the END of `endPatchDrag` (modelMove path)**

In `endPatchDrag` (~L15706), inside the function body — right before any `return` and after `dragState = null` — add:

```javascript
        snapModule.endDrag();
```

Make sure this fires for modelMove drags as well as patch drags (it's safe to always call; endDrag is idempotent on a null-active state).

- [ ] **Step 6: Manual smoke test in browser**

Restart server (per command in Conventions), open `STL_generate.html`, load `Tri_angle.cfdproj`, then:

1. Create a baffle next to the rotated rack.
2. Drag the baffle. Observe: it should snap when within ~10 cm of the rack face.
3. The baffle should NOT jitter — small pointer wiggles after snap should keep it locked.

If this works, proceed. If not, capture browser console + use `window.__SNAP_PROFILE = true` and share findings.

- [ ] **Step 7: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(snap): cut modelMove drag over to snapModule.updateDrag

Both 3D viewport drag call sites now use the new snap module. Grid snap
keeps its existing path (orthogonal feature). Snap delta is applied ONCE
per frame on top of the per-axis-gated anchor reset done above, fixing
the cumulative-snap jitter root cause. snapQuat (when rotation snap fires)
overwrites the model's quaternion in the same frame.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Add 4 toolbar toggles + localStorage persistence + migration

**Why:** Spec §3.1 + §7. User-facing controls for face/edge/vertex/rotation snap, persisted across sessions. Migrate legacy `state.snapFace` / `state.snapEdge` once.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add migration code that runs once at startup**

Inside the main IIFE, after `state` is initialized but before the snap module instantiation (Task 12 line), add:

```javascript
    // ── Migrate legacy snapFace / snapEdge to snapConfig (spec §7) ──
    function readStoredBool(key, legacy, dflt) {
        const ls = localStorage.getItem('stlgen.snapConfig.' + key);
        if (ls === 'true')  return true;
        if (ls === 'false') return false;
        if (typeof legacy === 'boolean') return legacy;
        return dflt;
    }
    state.snapConfig = {
        face:     readStoredBool('face',     state.snapFace, true),
        edge:     readStoredBool('edge',     state.snapEdge, true),
        vertex:   readStoredBool('vertex',   undefined,      false),
        rotation: readStoredBool('rotation', undefined,      false)
    };
    delete state.snapFace; delete state.snapEdge;
```

- [ ] **Step 2: Initialize the snap module's config from `state.snapConfig`**

In the snap module init block (Task 12 Step 2), AFTER `createSnapModule` returns, add:

```javascript
    snapModule.setConfig(state.snapConfig);
```

- [ ] **Step 3: Add 4 toolbar buttons**

Find the existing snap toolbar markup (Grep for `id="snap-face-btn"`). Replace the existing 2-3 button group with the 4-button group (keep the grid button alongside; it remains its own feature):

```html
<div class="snap-toolbar" style="display:flex; gap:2px; align-items:center;">
    <button id="snap-face-btn"     class="snap-btn" onclick="window.toggleSnapKind('face')"     title="Face snap (面对齐)">🔲 F</button>
    <button id="snap-edge-btn"     class="snap-btn" onclick="window.toggleSnapKind('edge')"     title="Edge snap (边对齐)">📏 E</button>
    <button id="snap-vertex-btn"   class="snap-btn" onclick="window.toggleSnapKind('vertex')"   title="Vertex snap (点对齐)">📍 V</button>
    <button id="snap-rotation-btn" class="snap-btn" onclick="window.toggleSnapKind('rotation')" title="Rotation snap (旋转对齐: 需激活旋转 gizmo)">🔄 R</button>
    <button id="snap-grid-btn"     class="snap-btn" onclick="window.toggleSnapGrid()"           title="Grid snap (网格)">⊞ G</button>
</div>
```

CSS — find the existing `.snap-btn` rule (or add this block in the `<style>` section near other toolbar styles):

```css
.snap-btn {
    padding: 4px 8px; background: #2a2a2a; border: 1px solid #444;
    border-radius: 3px; cursor: pointer; color: #ccc; font-size: 12px;
}
.snap-btn.active {
    background: #3a2e1a; border-color: #e88a2f;
    box-shadow: 0 0 4px rgba(232, 138, 47, 0.3); color: #fff;
}
.snap-btn:hover { background: #3a3a3a; }
```

- [ ] **Step 4: Replace `toggleSnapFace` / `toggleSnapEdge` with unified `toggleSnapKind`**

Find the old handlers (~L12436 `toggleSnapFace`, L12443 `toggleSnapEdge`) and replace BOTH with:

```javascript
    window.toggleSnapKind = function(kind) {
        if (!['face','edge','vertex','rotation'].includes(kind)) return;
        pushUndoCheckpoint('toggleSnap_' + kind);
        state.snapConfig[kind] = !state.snapConfig[kind];
        localStorage.setItem('stlgen.snapConfig.' + kind, String(state.snapConfig[kind]));
        snapModule.setConfig({ [kind]: state.snapConfig[kind] });
        // Grid snap and the new snap kinds are mutually exclusive
        if (state.snapConfig[kind]) state.snapGrid = false;
        refreshSnapButtons();
        logOperation('toggleSnap_' + kind, `${kind} snap: ${state.snapConfig[kind] ? 'on' : 'off'}`, {}, 'view');
    };
```

Update `refreshSnapButtons` to handle 4 buttons + the grid one:

```javascript
    function refreshSnapButtons() {
        const map = {
            face:     'snap-face-btn',
            edge:     'snap-edge-btn',
            vertex:   'snap-vertex-btn',
            rotation: 'snap-rotation-btn'
        };
        for (const k of Object.keys(map)) {
            const el = document.getElementById(map[k]);
            if (el) el.classList.toggle('active', !!state.snapConfig[k]);
        }
        const gridBtn = document.getElementById('snap-grid-btn');
        if (gridBtn) gridBtn.classList.toggle('active', !!state.snapGrid);
    }
```

Update `toggleSnapGrid` to clear the new snap config when grid is enabled:

```javascript
    window.toggleSnapGrid = function() {
        pushUndoCheckpoint('toggleSnapGrid');
        state.snapGrid = !state.snapGrid;
        if (state.snapGrid) {
            state.snapConfig.face = false; state.snapConfig.edge = false;
            state.snapConfig.vertex = false; state.snapConfig.rotation = false;
            snapModule.setConfig(state.snapConfig);
        }
        refreshSnapButtons();
        refreshGridToolbar();
        logOperation('toggleSnapGrid', `Grid snap: ${state.snapGrid ? 'on' : 'off'}`, {}, 'view');
    };
```

- [ ] **Step 5: Initial UI sync at startup**

Find where existing UI initialization happens (e.g. inside `init()` or near `updateUI()` first call). Add:

```javascript
    refreshSnapButtons();
```

- [ ] **Step 6: Manual verification in browser**

Restart server. Open the page. Verify:
- 4 buttons render in toolbar with correct active states (F + E should be lit by default).
- Click each button: state toggles, button highlights orange, console shows log line.
- Reload page: previous toggle states restored.
- Toggle grid → all 4 face/edge/vertex/rotation toggles deactivate.
- Toggle face → grid deactivates.

- [ ] **Step 7: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(snap): toolbar — 4 toggles (F/E/V/R) with localStorage persistence

Replaces the legacy face/edge buttons with a 4-button group. Each toggle
writes through to snapModule.setConfig and persists to localStorage under
stlgen.snapConfig.<key>. Migration: legacy state.snapFace/snapEdge are
read once and removed. Grid snap remains as its own (mutually exclusive)
toggle.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Visual feedback overlay (highlight + cursor badge)

**Why:** Spec §3.3. Without visual feedback, users can't see which target the snap is locked onto, especially in dense scenes.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add a dedicated `snapHighlightGroup` to the scene**

Find where `scene` is initialized and other top-level groups are added. Add:

```javascript
    const snapHighlightGroup = new THREE.Group();
    snapHighlightGroup.name = 'snapHighlight';
    scene.add(snapHighlightGroup);
```

- [ ] **Step 2: Implement the overlay rebuilder**

Add this function inside the IIFE (place it near other render-helper functions):

```javascript
    function rebuildSnapHighlight() {
        // Clear previous frame's overlay
        while (snapHighlightGroup.children.length) {
            const child = snapHighlightGroup.children.pop();
            if (child.geometry) child.geometry.dispose();
            if (child.material) child.material.dispose();
        }
        const hl = snapModule.getHighlight();
        if (!hl) return;
        const yellow = 0xffcc00;
        if (hl.kind === 'face') {
            // Quad: 4 corners from center ± u*sizeU/2 ± v*sizeV/2
            const c = hl.world.center, u = hl.world.u, v = hl.world.v;
            const su = hl.world.sizeU / 2, sv = hl.world.sizeV / 2;
            const p0 = c.clone().addScaledVector(u, -su).addScaledVector(v, -sv);
            const p1 = c.clone().addScaledVector(u,  su).addScaledVector(v, -sv);
            const p2 = c.clone().addScaledVector(u,  su).addScaledVector(v,  sv);
            const p3 = c.clone().addScaledVector(u, -su).addScaledVector(v,  sv);
            const geom = new THREE.BufferGeometry();
            geom.setAttribute('position', new THREE.Float32BufferAttribute([
                p0.x, p0.y, p0.z, p1.x, p1.y, p1.z, p2.x, p2.y, p2.z,
                p0.x, p0.y, p0.z, p2.x, p2.y, p2.z, p3.x, p3.y, p3.z
            ], 3));
            const mat = new THREE.MeshBasicMaterial({ color: yellow, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false });
            snapHighlightGroup.add(new THREE.Mesh(geom, mat));
            // Border outline
            const lineGeom = new THREE.BufferGeometry().setFromPoints([p0, p1, p2, p3, p0]);
            const lineMat = new THREE.LineBasicMaterial({ color: 0xff9900, linewidth: 2 });
            snapHighlightGroup.add(new THREE.Line(lineGeom, lineMat));
        } else if (hl.kind === 'edge') {
            const geom = new THREE.BufferGeometry().setFromPoints([hl.world.p0, hl.world.p1]);
            const mat = new THREE.LineBasicMaterial({ color: yellow, linewidth: 3 });
            snapHighlightGroup.add(new THREE.Line(geom, mat));
        } else if (hl.kind === 'vertex') {
            const geom = new THREE.BufferGeometry();
            geom.setAttribute('position', new THREE.Float32BufferAttribute([hl.world.point.x, hl.world.point.y, hl.world.point.z], 3));
            const mat = new THREE.PointsMaterial({ color: yellow, size: 12, sizeAttenuation: false });
            snapHighlightGroup.add(new THREE.Points(geom, mat));
        }
        updateCursorBadge(hl);
    }
```

- [ ] **Step 3: Add a DOM cursor badge**

In the HTML body, add a hidden absolutely-positioned div near the `<body>` opening:

```html
<div id="snap-cursor-badge" style="display:none; position:fixed; pointer-events:none; z-index:10000; background:#ff9900; color:#222; font-weight:bold; font-size:11px; padding:2px 6px; border-radius:8px; transform:translate(12px, -22px);"></div>
```

Add the JS update helper:

```javascript
    function updateCursorBadge(hl) {
        const badge = document.getElementById('snap-cursor-badge');
        if (!badge) return;
        if (!hl) { badge.style.display = 'none'; return; }
        const letter = hl.kind === 'face' ? 'F' : hl.kind === 'edge' ? 'E' : 'V';
        badge.textContent = letter + (hl.degraded ? '*' : '');
        badge.style.display = 'inline-block';
    }

    // Track cursor position to anchor the badge
    document.addEventListener('mousemove', (e) => {
        const badge = document.getElementById('snap-cursor-badge');
        if (badge && badge.style.display !== 'none') {
            badge.style.left = e.clientX + 'px';
            badge.style.top  = e.clientY + 'px';
        }
    });
```

- [ ] **Step 4: Call `rebuildSnapHighlight()` from the drag-update path**

In the modelMove drag code (right after the snap apply block from Task 13), add:

```javascript
    rebuildSnapHighlight();
```

Also call `rebuildSnapHighlight()` (which will clear it) from `endPatchDrag()` after `snapModule.endDrag()`.

- [ ] **Step 5: Manual verification**

Restart server, open Tri_angle, drag a baffle near the rotated rack. Expect:
- Yellow translucent quad appears on the rack face that snap locks to.
- Orange "F" badge appears next to the cursor.
- Drag away (>15 cm): overlay disappears, badge hides.

- [ ] **Step 6: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(snap): visual feedback — face/edge/vertex highlight overlay + cursor badge

Renders a yellow translucent quad (face), line (edge), or point (vertex)
on the snapped target each frame. DOM badge follows the cursor showing
the snap kind (F/E/V) and a `*` suffix when in fallback mode. Cleared
on drag end and on every frame the snap candidate becomes null.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Wire rotation-gizmo-active flag through `beginDrag`

**Why:** Spec §3.2. Rotation snap's "gizmo active" condition needs to be passed to `snapModule.beginDrag`. Currently we hardcoded `false` in Task 13.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Find the rotation gizmo state**

Grep for the function that activates the rotation gizmo (search `_rotateGizmos` or `rotation-tool-active` class). Identify a boolean or selector that tells whether the user is in rotate mode.

A reliable check: examine `state.tool` or the existence of an active rotation-mode flag. Likely it's something like `state.activeTool === 'rotate'` or checking that `_rotateGizmos.length > 0` and they are visible.

- [ ] **Step 2: Define a helper**

Inside the IIFE, add (near other tool helpers):

```javascript
    function isRotationGizmoActive() {
        // True when the rotation gizmo is mounted on the currently-selected model
        // AND the user has the rotate tool selected. Snap module uses this to
        // gate rotation snap (spec §3.2 condition 2).
        return !!(_rotateGizmos && _rotateGizmos.length > 0
            && _rotateGizmos[0].rings && _rotateGizmos[0].rings.length > 0);
    }
```

If `_rotateGizmos` does not exist or has a different name, replace the body with the equivalent — confirm via Grep before writing.

- [ ] **Step 3: Pass the flag through `beginDrag`**

In the `beginModelMoveDrag` body where we call `snapModule.beginDrag(...)` (added in Task 13 Step 2), change:

```javascript
            snapModule.beginDrag(dragState.modelIds || [dragState.modelId], {
                rotationGizmoActive: false
            });
```

to:

```javascript
            snapModule.beginDrag(dragState.modelIds || [dragState.modelId], {
                rotationGizmoActive: isRotationGizmoActive()
            });
```

- [ ] **Step 4: Suppress rotation snap during gizmo drag itself**

In `beginRotationGizmoDrag` (find via Grep), add at the END:

```javascript
        // While the user is actively rotating with the gizmo, snap rotation
        // correction must NOT fire (otherwise it overrides the explicit shift-15° step).
        snapModule.endDrag();
```

- [ ] **Step 5: Manual smoke test**

Restart server. Tri_angle scenario:
1. Select a baffle, click rotate tool — gizmo appears.
2. Manually rotate baffle to ~44° (close to but not exactly 45°).
3. Click move tool, enable F + R toggles.
4. Drag baffle toward the 45°-rotated rack face.
5. Expect: baffle's quaternion corrects to exactly 45°, then face snaps coplanar.

- [ ] **Step 6: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(snap): pass isRotationGizmoActive into beginDrag for rotation snap gating

Adds isRotationGizmoActive() helper and threads its result into
snapModule.beginDrag. Active gizmo-drag path calls snapModule.endDrag() so
shift-15° steps are not overridden by rotation snap.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Performance instrumentation hook

**Why:** Spec §5.4. Opt-in profiling for verifying P50/P95/P99 against the budget.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.js`
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Write failing test for the profiling hook**

Append to `snap.test.js`:

```javascript
test('updateDrag respects window.__SNAP_PROFILE flag (push timing into __snapTimings)', () => {
    const { createSnapModule } = require('./snap.js');
    const moving = makeOBBModel(THREE, 'M', 0, 0, 0, 0.5, 0.5, 0.5);
    const peer   = makeOBBModel(THREE, 'P', 0.05, 0, 0, 0.5, 0.5, 0.5);
    const models = [moving, peer];
    const fakeGlobal = { __SNAP_PROFILE: true, __snapTimings: [] };
    const api = createSnapModule({
        getModelById: id => models.find(m => m.id === id) || null,
        getModelOBB:  id => (models.find(m => m.id === id) || {}).obb || null,
        getAllModels: () => models,
        getCameraDir: () => new THREE.Vector3(0, 0, -1),
        THREE,
        profileTarget: fakeGlobal           // injected for testability
    });
    api.beginDrag(['M'], { rotationGizmoActive: false });
    api.updateDrag(new THREE.Vector3());
    assert.equal(fakeGlobal.__snapTimings.length, 1);
    assert.ok(fakeGlobal.__snapTimings[0] >= 0);
});
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement profiling hook**

In `snap.js`, modify `createSnapModule` signature to accept `profileTarget`:

```javascript
    function createSnapModule(deps) {
        const { getModelById, getModelOBB, getAllModels, getCameraDir, THREE, profileTarget } = deps;
        // ... existing ...
        const profile = profileTarget || (typeof window !== 'undefined' ? window : null);
```

Wrap the `updateDrag` body with timing:

```javascript
            updateDrag(_pointerDelta) {
                const _t0 = profile && profile.__SNAP_PROFILE ? performance.now() : 0;
                try {
                    // ... existing implementation body ...
                } finally {
                    if (profile && profile.__SNAP_PROFILE) {
                        if (!profile.__snapTimings) profile.__snapTimings = [];
                        profile.__snapTimings.push(performance.now() - _t0);
                    }
                }
            }
```

Note: `performance.now()` is available in Node 18+ as a global.

- [ ] **Step 4: Run, verify PASS** (34 tests)

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/js/snap.js STLGeneratorForOpenFoam/web/js/snap.test.js
git commit -m "$(cat <<'EOF'
feat(snap): opt-in performance instrumentation via window.__SNAP_PROFILE

When window.__SNAP_PROFILE is true, every updateDrag call appends its
duration in ms to window.__snapTimings. Off by default (zero overhead).
Developer enables via console: window.__SNAP_PROFILE = true.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Cleanup — remove legacy snap implementation

**Why:** Spec §1.3. The old `computeProximitySnap`, `initSnapCache`, `querySnapCandidates`, `_lastSnapResult`, `_SNAP_THROTTLE_MS` and related state are now dead code. Remove them and the legacy state field reads.

**Files:**
- Modify: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Verify no remaining call sites for the old functions**

```bash
grep -n "computeProximitySnap\|initSnapCache\|querySnapCandidates" /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: matches only inside the function bodies being deleted (no callers).

- [ ] **Step 2: Delete the function bodies and supporting state**

Delete in this order (verify each block is contiguous; line numbers will drift after each deletion):

1. `function computeProximitySnap(...) { ... }` (~L12545-L12652)
2. `function initSnapCache() { ... }` and `function querySnapCandidates(...) { ... }` and `_lastSnapResult` / `_lastSnapTime` / `_SNAP_THROTTLE_MS` declarations (~L12468-L12543)
3. The old initSnapCache call sites at ~L15234 and ~L15279 (replace with no-op or just delete; snap module's own grid handles it)
4. The throttled snap recompute block at ~L15430 (`if ((state.snapFace || state.snapEdge) && (performance.now() - _lastSnapTime >= _SNAP_THROTTLE_MS))` — entire block) — superseded by snap module's per-frame call

- [ ] **Step 3: Remove any remaining references to the deleted symbols**

```bash
grep -n "_lastSnap\|_SNAP_THROTTLE\|computeProximitySnap\|initSnapCache\|querySnapCandidates\|state\.snapFace\|state\.snapEdge" /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: no matches. If any remain, fix them in this same task.

- [ ] **Step 4: Manual smoke test — full snap pipeline**

Restart server. Verify every smoke-test item from spec §5.3:

```
☐ Open Tri_angle.cfdproj; create baffle next to the 45°-rotated rack.
☐ Select baffle → activate rotation gizmo → manually rotate ~45°.
☐ Enable F + R in toolbar.
☐ Switch to move; drag baffle toward rack side face.
☐ Verify: baffle rotation corrects to exactly 45°.
☐ Verify: baffle face snaps flush to rack face.
☐ Verify: yellow face overlay drawn on rack side.
☐ Verify: cursor shows F badge.
☐ Wiggle pointer; baffle remains stable (no jitter).
☐ Drag far (>15 cm) → baffle releases and follows cursor again.
☐ Ctrl+Z once → reverts both position and rotation in one step.
☐ Disable R; retry → position snaps flush, rotation unchanged.
☐ Disable F, enable E → only edges snap.
☐ Enable V → three types independently gateable.
```

If any item fails, file follow-up tasks.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
chore(snap): remove legacy computeProximitySnap / initSnapCache / state.snapFace

The new snap.js module fully owns face/edge/vertex/rotation snap. Deletes
the inline implementation (~200 lines) plus the throttle and cache state
that came with it. Legacy state.snapFace / state.snapEdge are gone (the
migration in Task 14 already moved their values into state.snapConfig +
localStorage).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Spec Coverage Self-Check

| Spec section | Implemented in task |
|---|---|
| §1.1 file layout | Tasks 1, 2 |
| §1.2 module shape | Tasks 2, 11, 17 |
| §1.3 replacing existing code | Tasks 13, 18 |
| §1.4 candidate data model | Tasks 3, 5, 6, 7 |
| §2.1 constants | Task 2 |
| §2.2 OBB → primitives | Task 3 |
| §2.3 broad phase grid | Task 4 |
| §2.4 narrow filter + scoring | Tasks 5, 6, 7, 8 |
| §2.5 hysteresis state machine | Tasks 8, 11 |
| §2.6 single-frame correction | Task 9 (helper), Task 13 (caller pattern) |
| §2.7 rotation snap | Task 10, gating in Tasks 11, 16 |
| §2.8 early termination | Task 8 (within pickBest) |
| §3.1 toolbar buttons | Task 14 |
| §3.2 rotation-snap activation rule | Tasks 10, 16 |
| §3.3 visual feedback | Task 15 |
| §3.4 undo/redo (no new code needed) | — covered by existing pushUndoCheckpoint |
| §4.1-§4.5 perf | Tasks 4, 11 (fallback) |
| §4.6 memory envelope | Task 11 (cache invalidation) |
| §5.1 unit tests | Tasks 1-11, 17 (all TDD) |
| §5.2 integration tests | Task 13 manual smoke + Task 18 final smoke |
| §5.3 manual smoke checklist | Task 18 |
| §5.4 perf instrumentation | Task 17 |
| §6 edge cases | Verified in Tasks 3 (degenerate), 11 (empty scene), 18 (camera rotate during drag, NaN guards via expandOBB) |
| §7 migration | Task 14 |

No spec gaps remain.
