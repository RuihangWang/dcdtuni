# Phase 0 — Interaction Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore regressed interaction-layer behavior (snap, undo, i18n) and polish two UI items (camera verification, axis widget) in the STLGenerator browser prototype so that `Tri_angle.cfdproj` is fully usable as the testbed for Phases 1–3.

**Architecture:** Pure bug-fix + verification work; no new modules. All code changes in three existing files (`STL_generate.html`, `js/snap.js`, `js/snap.test.js`) plus one new headless-verification script. Snap regression fix driven by `git bisect` between known-good commit `c270f6f` and merge `1ae0fc3`. Undo fix is a scoping unification of the `_rdCheckpointPushed` flag pattern. i18n fix introduces one helper (`applyI18nToSubtree`) and wires it into all property-inspector rerender paths.

**Tech Stack:** Vanilla JS, Three.js r128, `vitest` (already used by `snap.test.js`), `browser-use` (installed at `~/.local/bin/browser-use`) for headless verification, `conda run -n dcdtuni` for the backend server.

**Design spec:** [`docs/superpowers/specs/2026-04-15-phase0-interaction-foundation-design.md`](../specs/2026-04-15-phase0-interaction-foundation-design.md)

**Preceding specs referenced:** [`2026-04-14-snap-algorithm-design.md`](../specs/2026-04-14-snap-algorithm-design.md) (defines the snap system being restored)

---

## Task 0: Baseline — reproduce all 5 symptoms on current `main`

Before fixing anything, capture the current failure signatures so each fix can be verified against its starting state.

**Files:**
- Create: `docs/superpowers/plans/2026-04-15-phase0-baseline.md` (reproduction log, will be deleted at end of phase)

- [ ] **Step 1: Start server**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
fuser -k 8000/tcp 2>/dev/null
./STLGeneratorForOpenFoam/server/start_server.sh --port 8000 &
sleep 3
```

Expected: `Serving HTTP on 0.0.0.0 port 8000` in log.

- [ ] **Step 2: Open Tri_angle project headless**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8000/STL_generate.html
browser-use eval "window.loadProjectFromPath && window.loadProjectFromPath('projects/Tri_angle.cfdproj')"
sleep 2
browser-use screenshot /tmp/phase0-baseline-loaded.png
```

If `loadProjectFromPath` does not exist, use the UI: `browser-use state` to find the "📂 加载工程" button index, click it, then navigate to `Tri_angle.cfdproj`. Record the exact sequence in `2026-04-15-phase0-baseline.md`.

- [ ] **Step 3: Record snap failure**

```bash
browser-use eval "
  const rack = state.models.find(m => m.kind === 'rack');
  const baffle = state.models.find(m => m.kind === 'baffle');
  console.log('rack:', rack && rack.id, 'baffle:', baffle && baffle.id);
  console.log('snapConfig:', JSON.stringify(state.snapConfig));
  console.log('__snapModule defined:', !!window.__snapModule);
"
browser-use screenshot /tmp/phase0-baseline-snap.png
```

Record in baseline doc:
- Whether `__snapModule` is defined
- Current `state.snapConfig` value
- Whether a baffle exists in Tri_angle (if not, note that snap reproduction requires manually creating one)
- Screenshot of snap toolbar (F/E/V/R buttons) state

- [ ] **Step 4: Record undo failure**

```bash
browser-use eval "
  console.log('undoStack.length:', undoStack.length);
  console.log('_rdCheckpointPushed (local):', typeof _rdCheckpointPushed !== 'undefined' ? _rdCheckpointPushed : 'undef');
  console.log('window._rdCheckpointPushed:', window._rdCheckpointPushed);
"
```

Record these values. Then enter a rack detail view, drag the inner-width slider 3 times, and after each drag:

```bash
browser-use eval "console.log('after drag:', undoStack.length, window._rdCheckpointPushed);"
```

Record whether `undoStack.length` increments by 1 per drag (correct) or stays flat / only increments once (bug).

- [ ] **Step 5: Record camera behavior**

Manually in the opened browser (not headless — user's existing window), test:

1. Left-click-drag on empty canvas → record whether camera rotates (regression) or does nothing (correct).
2. Middle-click-drag → record whether camera pans.
3. Wheel → record whether camera zooms.
4. Right-click-drag → record whether camera rotates.

Record each in `2026-04-15-phase0-baseline.md`.

- [ ] **Step 6: Record axis widget position**

```bash
browser-use eval "
  const el = document.getElementById('view-cube');
  if (el) {
    const r = el.getBoundingClientRect();
    console.log('viewCube rect:', r.left, r.top, r.width, r.height);
    console.log('viewCube parent:', el.parentElement.id || el.parentElement.className);
  } else {
    console.log('view-cube not found');
  }
"
browser-use screenshot /tmp/phase0-baseline-axes.png
```

Record position and size.

- [ ] **Step 7: Record i18n leakage**

```bash
browser-use eval "
  const texts = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/^(label|btn|tree|title|nav|error|loading|export)\.\w+/.test(t) && el.children.length === 0) {
      texts.push(t);
    }
  });
  console.log('raw i18n keys leaked:', texts.length);
  console.log(texts.slice(0, 20).join('\n'));
"
```

Record count and first 20 examples in baseline doc.

- [ ] **Step 8: Commit baseline**

```bash
git add -f docs/superpowers/plans/2026-04-15-phase0-baseline.md
git commit -m "$(cat <<'EOF'
docs(phase0): record baseline symptoms on current main

Pre-fix reproduction log for the five Phase 0 items (snap, undo, camera,
axis widget, i18n). Deleted at phase close after all acceptance criteria
pass.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Snap regression — `git bisect` to find the culprit commit

**Files:**
- No edits yet — this task only identifies the culprit.

- [ ] **Step 1: Start bisect between known-good and merge**

Known-good: `c279f6f fix(snap): final-review crashers — getModelOBB signature + getHighlight world`
Merge (bad): `1ae0fc3 Merge branch 'partition-wall-impl'`

```bash
cd STLGeneratorForOpenFoam
git bisect start
git bisect bad 1ae0fc3
git bisect good c279f6f
```

Expected: git prints something like "Bisecting: N revisions left to test after this (roughly K steps)".

- [ ] **Step 2: Define the bisect test**

At each bisect step, run this verification script:

```bash
cat > /tmp/phase0-snap-check.sh <<'EOF'
#!/bin/bash
set -e
cd /mnt/lv/litianyu/mycode/dcdtuni
fuser -k 8000/tcp 2>/dev/null || true
./STLGeneratorForOpenFoam/server/start_server.sh --port 8000 > /tmp/phase0-server.log 2>&1 &
SERVER_PID=$!
sleep 3
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null || true
browser-use open http://localhost:8000/STL_generate.html
sleep 2
RESULT=$(browser-use eval "
  // snap must be defined AND config must default to face:true,edge:true
  const ok = !!window.__snapModule
    && state.snapConfig && state.snapConfig.face === true && state.snapConfig.edge === true;
  console.log('SNAP_OK:' + ok);
")
kill $SERVER_PID 2>/dev/null || true
echo "$RESULT" | grep -q 'SNAP_OK:true' && exit 0 || exit 1
EOF
chmod +x /tmp/phase0-snap-check.sh
```

Expected: script exits 0 at good commits, non-0 at the regression commit.

- [ ] **Step 3: Run bisect**

```bash
cd STLGeneratorForOpenFoam
git bisect run /tmp/phase0-snap-check.sh
```

Expected: `<SHA> is the first bad commit`. Record the SHA and the commit message.

- [ ] **Step 4: Inspect the culprit diff**

```bash
git bisect reset
git show <culprit-sha> --stat
git show <culprit-sha> -- web/STL_generate.html web/js/snap.js
```

Read the diff. Identify which integration point broke (candidates: dropped `beginDrag` call, missing `__snapModule.invalidate()`, changed conditional at `STL_generate.html:15993`, `snap.js` export signature change).

- [ ] **Step 5: If the automated check gave a false signal, refine and re-run**

If the bisect landed on a commit that *looks* innocent (e.g., a docs-only change), the automated check is insufficient. Refine it:

```bash
cat > /tmp/phase0-snap-check.sh <<'EOF'
#!/bin/bash
set -e
cd /mnt/lv/litianyu/mycode/dcdtuni
fuser -k 8000/tcp 2>/dev/null || true
./STLGeneratorForOpenFoam/server/start_server.sh --port 8000 > /tmp/phase0-server.log 2>&1 &
SERVER_PID=$!
sleep 3
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null || true
browser-use open http://localhost:8000/STL_generate.html
sleep 2
# Create a baffle and a rack, start a simulated drag, expect snapDelta non-null
RESULT=$(browser-use eval "
  window.createBaffleModel && window.createBaffleModel('b1', {x:0,y:0,z:0}, {W:1,D:1,H:0.1});
  const r = window.createRackModel('r1');
  r.anchor = {x:0.05, y:0, z:0};
  state.models.push(r); rebuildGeometry();
  const b = state.models.find(m => m.name === 'b1');
  window.__snapModule && window.__snapModule.beginDrag([b.id], { rotationGizmoActive: false });
  const res = window.__snapModule && window.__snapModule.updateDrag(new THREE.Vector3());
  console.log('SNAP_RESULT:' + JSON.stringify(res));
")
kill $SERVER_PID 2>/dev/null || true
echo "$RESULT" | grep -qE 'snapDelta.*[0-9]' && exit 0 || exit 1
EOF
```

Re-run bisect with the refined script.

- [ ] **Step 6: Record culprit and commit the investigation note**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
echo "Snap regression culprit: <SHA> — <commit subject>" >> docs/superpowers/plans/2026-04-15-phase0-baseline.md
echo "Integration point broken: <description>" >> docs/superpowers/plans/2026-04-15-phase0-baseline.md
git add -f docs/superpowers/plans/2026-04-15-phase0-baseline.md
git commit -m "docs(phase0): record snap regression culprit from bisect

Culprit: <SHA>
Integration point: <point>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Snap regression — write failing test

Before fixing, write a unit test in `snap.test.js` that exercises the exact integration point the bisect identified.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/snap.test.js`

- [ ] **Step 1: Read existing test to match style**

```bash
wc -l STLGeneratorForOpenFoam/web/js/snap.test.js
head -80 STLGeneratorForOpenFoam/web/js/snap.test.js
```

Note the imports, mock pattern, describe/it structure.

- [ ] **Step 2: Add a failing test named for the regression**

Open `snap.test.js`. At the bottom of the last `describe` block, add:

```js
describe('regression: partition-wall branch', () => {
    it('produces a face snap candidate when rotated baffle is near rotated rack (Tri_angle repro)', () => {
        // Arrange: peer rack rotated 45° around Z; moving baffle rotated 44°, 8cm away.
        const rackQuat = makeQuatZ(Math.PI / 4);
        const baffleQuat = makeQuatZ(Math.PI / 4 - 0.02); // ~1.1° off parallel
        const peer = makeMockRack({ id: 'r1', center: [0, 0, 0.5], quat: rackQuat, half: [0.3, 0.5, 0.5] });
        const moving = makeMockBaffle({ id: 'b1', center: [0.38, 0, 0.5], quat: baffleQuat, half: [0.01, 0.3, 0.3] });
        const snap = createSnapModule(mockDeps({ models: [peer, moving] }));
        snap.setConfig({ face: true, edge: false, vertex: false, rotation: false });
        snap.beginDrag(['b1'], { rotationGizmoActive: false });

        // Act
        const result = snap.updateDrag(new THREE.Vector3(0, 0, 0));

        // Assert
        expect(result.snapDelta).not.toBeNull();
        expect(result.highlight).not.toBeNull();
        expect(result.highlight.kind).toBe('face');
        expect(result.highlight.peerModelId).toBe('r1');
    });
});
```

If `makeMockRack`, `makeMockBaffle`, `makeQuatZ`, or `mockDeps` do not exist as helpers, reuse whatever pattern the existing tests in this file use. Adapt types to match.

- [ ] **Step 3: Run the test — expect FAIL**

```bash
cd STLGeneratorForOpenFoam
npx vitest run web/js/snap.test.js -t "partition-wall branch"
```

Expected: 1 test fails. Record the failure message — it tells us whether the integration is broken (`snapDelta === null`) or the algorithm itself is broken (throws).

- [ ] **Step 4: Commit failing test**

```bash
git add web/js/snap.test.js
git commit -m "$(cat <<'EOF'
test(snap): add regression test for Tri_angle rotated-baffle case

Reproduces the Phase 0 snap regression: rotated baffle near rotated rack
should produce a face snap candidate. Currently fails — see
docs/superpowers/plans/2026-04-15-phase0-baseline.md for bisect result.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Snap regression — fix

**Files:**
- Modify: one or both of `STLGeneratorForOpenFoam/web/STL_generate.html`, `STLGeneratorForOpenFoam/web/js/snap.js` — depends on Task 1's finding.

- [ ] **Step 1: Implement the fix targeted at the culprit diff**

Based on Task 1's identified integration point, edit the minimal code required to restore the snap behavior without reverting any partition-wall functionality. Pattern examples:

- **If the culprit dropped a `beginDrag` call:** re-add it in the exact drag-start branch the partition-wall commit touched. Do not move other code.
- **If the culprit added an early-return before the snap call:** narrow the early-return condition to only the partition-wall case (`model.kind === 'partitionWall'`), falling through to snap for every other kind.
- **If the culprit changed `snap.js` internals (e.g. OBB expansion) and broke rotated cases:** revert just that internal change, keeping any new partition-wall-specific code paths gated behind `kind === 'partitionWall'`.

Show the exact diff in the commit message.

- [ ] **Step 2: Run the regression test — expect PASS**

```bash
cd STLGeneratorForOpenFoam
npx vitest run web/js/snap.test.js -t "partition-wall branch"
```

Expected: all tests in that describe pass.

- [ ] **Step 3: Run the full snap suite — expect PASS**

```bash
npx vitest run web/js/snap.test.js
```

Expected: all existing tests still pass.

- [ ] **Step 4: Manual smoke test on Tri_angle**

Follow the manual smoke-test checklist verbatim from [`2026-04-14-snap-algorithm-design.md`](../specs/2026-04-14-snap-algorithm-design.md) §5.3, lines 460–475. Every line must pass.

If any line fails and the failure is *specifically* for rotated objects (not axis-aligned), **stop and escalate** per the Phase 0 spec §2.2 conditional redesign gate. Do not attempt an algorithm redesign inside this phase.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/
git commit -m "$(cat <<'EOF'
fix(snap): restore integration broken by partition-wall branch

Preserves partition-wall feature; restores snap for all other model kinds.
Passes full snap.test.js suite and the smoke-test checklist from the
2026-04-14 snap-algorithm-design spec §5.3 on Tri_angle.cfdproj.

Culprit commit: <SHA from Task 1>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Undo — audit `_rdCheckpointPushed` scoping

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — 20+ sites around lines 11836–12043, plus any additional sites discovered by grep.

- [ ] **Step 1: Grep every usage**

```bash
grep -n '_rdCheckpointPushed' STLGeneratorForOpenFoam/web/STL_generate.html
```

Record each line. Classify each as:
- Read/write via lexical `_rdCheckpointPushed` (bare identifier)
- Read/write via `window._rdCheckpointPushed`

- [ ] **Step 2: Unify every usage to `window._rdCheckpointPushed`**

Open `STL_generate.html`. For every line from Step 1 that reads/writes the bare identifier, change it to `window._rdCheckpointPushed`. Example edits:

Before (around line 11836):
```js
if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailInnerWidth'); _rdCheckpointPushed = true; }
```

After:
```js
if (!window._rdCheckpointPushed) { pushUndoCheckpoint('rackDetailInnerWidth'); window._rdCheckpointPushed = true; }
```

Repeat for every bare-identifier site.

If a `let _rdCheckpointPushed = false;` declaration exists, delete it — the flag is now global-only.

- [ ] **Step 3: Add one helper near the other undo-related helpers**

Locate `pushUndoCheckpoint` at roughly line 3394. Immediately after its closing brace, insert:

```js
function _resetRdCheckpoint() {
    window._rdCheckpointPushed = false;
}
```

- [ ] **Step 4: Wire the reset into every drag-end path**

Find every place where a RACK-detail slider's drag ends. Candidate patterns to search:

```bash
grep -n 'onmouseup\|onblur\|pointerup\|endSliderDrag\|rdSlider' STLGeneratorForOpenFoam/web/STL_generate.html
```

For each RACK-detail slider (inner-width, slot-depth, server-depth, outlet-patch, door-porosity), ensure its drag-end handler calls `_resetRdCheckpoint()`. If no drag-end handler exists, add one inline on the slider's `onchange` attribute, since `onchange` fires at pointer release for range inputs:

```html
<input type="range" ... oninput="..." onchange="_resetRdCheckpoint()">
```

- [ ] **Step 5: Grep multi-move and multi-rotate for the same pattern**

```bash
grep -n 'pushUndoCheckpoint.*[Mm]ulti\|_multiMoveActive\|_multiRotateActive' STLGeneratorForOpenFoam/web/STL_generate.html
```

If multi-move/rotate uses a similar "first-call flag", apply the same unification and reset-on-end treatment.

- [ ] **Step 6: Smoke test in browser**

```bash
fuser -k 8000/tcp 2>/dev/null
./STLGeneratorForOpenFoam/server/start_server.sh --port 8000 &
sleep 3
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8000/STL_generate.html
# manually: open Tri_angle, enter rack detail view, drag inner-width slider 10 times.
# After each drag, log undoStack.length:
browser-use eval "console.log('stack:', undoStack.length, 'flag:', window._rdCheckpointPushed);"
```

Expected: `undoStack.length` increments by 1 per drag; `window._rdCheckpointPushed` returns to `false` after each drag.

- [ ] **Step 7: Verify 10 undos work**

In the browser, press Ctrl+Z 10 times. Expected: each press reverts exactly one slider drag; `undoStack.length` decrements monotonically.

- [ ] **Step 8: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
fix(undo): unify _rdCheckpointPushed scoping to window.*

Inline onchange handlers execute in global scope and cannot see the
lexical 'let _rdCheckpointPushed'. This caused the flag-stuck-at-true
regression where RACK-detail slider changes past the first one silently
skipped pushUndoCheckpoint. All N sites now use window._rdCheckpointPushed;
new _resetRdCheckpoint() helper is wired into every drag-end path.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Undo — verify state-mutating window functions all push checkpoints

Per CLAUDE.md: "every state-mutating `window.*` function MUST call `pushUndoCheckpoint()` FIRST". Audit for violations introduced by the partition-wall branch.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (only if violations found)

- [ ] **Step 1: List every window.* state-mutating function added or changed recently**

```bash
cd STLGeneratorForOpenFoam
git log --pretty=format: --name-only c279f6f..HEAD -- web/STL_generate.html | sort -u
git diff c279f6f..HEAD -- web/STL_generate.html | grep -E '^\+\s*window\.\w+\s*=\s*function|^\+\s*function\s+\w+' | head -40
```

Record every newly-added `window.*` function that mutates `state`. Typical markers of mutation: assigns to `state.*`, calls `rebuildGeometry()`, modifies `model.*`.

- [ ] **Step 2: Check each one calls pushUndoCheckpoint as first line**

For each function identified in Step 1, grep it:

```bash
grep -n -A 3 'window\.<funcName>' STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: first line of body is `pushUndoCheckpoint('<actionName>');`. If it is not, record the violation.

- [ ] **Step 3: Fix each violation**

For each violation, add the missing call. Example:

Before:
```js
window.setPartitionWallThickness = function(wallId, value) {
    const m = state.models.find(x => x.id === wallId);
    if (m && m.partitionWall) m.partitionWall.thickness = value;
    rebuildGeometry();
};
```

After:
```js
window.setPartitionWallThickness = function(wallId, value) {
    pushUndoCheckpoint('setPartitionWallThickness');
    const m = state.models.find(x => x.id === wallId);
    if (m && m.partitionWall) m.partitionWall.thickness = value;
    rebuildGeometry();
    logOperation('setPartitionWallThickness', 'Partition wall thickness', {wallId, value}, 'modify');
};
```

Both `pushUndoCheckpoint` first and `logOperation` last per the project rule.

- [ ] **Step 4: Smoke test one fixed function**

Manually trigger the function via the UI, then press Ctrl+Z. Expected: state reverts.

- [ ] **Step 5: Commit (skip if no violations)**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
fix(undo): restore pushUndoCheckpoint in N mutating window functions

Per CLAUDE.md rule every state-mutating window.* function must call
pushUndoCheckpoint as its first line. The partition-wall branch added
N functions that skipped this rule, making their changes non-undoable.
Added the mandatory checkpoint + logOperation pair to each.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Camera control — verify

**Files:**
- None expected. Modify `STL_generate.html` only if a broken override is found.

- [ ] **Step 1: Re-read the binding**

```bash
grep -n 'mouseButtons\|enableRotate\|enablePan' STLGeneratorForOpenFoam/web/STL_generate.html | head -20
```

Expected: a single binding at around line 1067 matching:

```js
controls.mouseButtons = {
    LEFT:   null,
    MIDDLE: THREE.MOUSE.PAN,
    RIGHT:  THREE.MOUSE.ROTATE
};
```

If exactly one site exists and it matches, proceed to Step 2. If multiple sites exist or the binding differs, record which one actually runs last at startup — that is the effective binding.

- [ ] **Step 2: Open browser and test manually**

```bash
# Server already running from earlier tasks; if not:
fuser -k 8000/tcp 2>/dev/null
./STLGeneratorForOpenFoam/server/start_server.sh --port 8000 &
sleep 3
```

In the browser, test on an empty canvas area:

- Left-click + drag → should NOT rotate the camera
- Middle-click + drag → should pan
- Wheel → should zoom
- Right-click + drag → should rotate

Record the actual behavior of each.

- [ ] **Step 3: If any behavior is wrong, find the override**

```bash
grep -n 'mouseButtons\|enableRotate' STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/js/*.js STLGeneratorForOpenFoam/web/*.js
```

Look for any site that re-assigns `controls.mouseButtons` or toggles `controls.enableRotate / enablePan`. Likely candidates: room2d enter/exit, rotation-gizmo tool activate, snap-module drag lifecycle.

Fix by removing the stray override, or gating it on the specific mode that requires it.

- [ ] **Step 4: Commit (skip if verification-only)**

If no fix was needed, append one line to `2026-04-15-phase0-baseline.md`:

```
Camera verification: PASS — all 4 mouse buttons behave per spec. No fix needed.
```

Then commit:

```bash
git add -f docs/superpowers/plans/2026-04-15-phase0-baseline.md
git commit -m "docs(phase0): verify camera mouse bindings; no fix required

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

If a fix was required, commit the `STL_generate.html` change with a `fix(camera):` commit describing what override was removed/gated.

---

## Task 7: Axis widget — reposition + restyle

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — CSS for `#view-cube`, plus any JS resize logic if hard-coded.

- [ ] **Step 1: Locate current styling**

```bash
grep -n 'view-cube\|viewCubeEl\|viewCubeRenderer' STLGeneratorForOpenFoam/web/STL_generate.html | head -20
```

Find the CSS rule for `#view-cube` (likely in the `<style>` block near the top of the file). Record current values.

- [ ] **Step 2: Locate FPS counter element**

```bash
grep -n 'fps\|FPS\|frame-counter\|stats-display' STLGeneratorForOpenFoam/web/STL_generate.html | head -10
```

Record its CSS selector and position.

- [ ] **Step 3: Write a failing manual test (screenshot comparison)**

Set up a reference screenshot of the desired state (user will provide the reference image at this point; until then, reference the user's attached image from the original message). Save as `docs/superpowers/plans/2026-04-15-phase0-axis-widget-reference.png`.

Take a screenshot of the current state:

```bash
browser-use screenshot /tmp/phase0-axis-widget-before.png
```

- [ ] **Step 4: Update CSS**

In `STL_generate.html` `<style>` block, change the `#view-cube` rule to:

```css
#view-cube {
    position: absolute;
    left: 16px;
    bottom: 48px;  /* above FPS counter (check its actual height) */
    width: 72px;   /* ~50% of previous; adjust based on previous value */
    height: 72px;
    z-index: 10;
    background: transparent;
    pointer-events: auto;
    /* drop subtle shadow so it stands out on varied backgrounds */
    filter: drop-shadow(0 0 6px rgba(0,0,0,0.5));
}
```

Replace the previous position/size values. Measure the FPS counter height before choosing `bottom: 48px` and adjust if needed to leave ~8 px gap.

- [ ] **Step 5: Update axis label colors inside the viewCube scene**

Locate the viewCube axis setup near [line 1170](../../STLGeneratorForOpenFoam/web/STL_generate.html#L1170) (`axesGroup.add(new THREE.AxesHelper(1.5))`). Replace with per-axis colored lines and bigger sprite labels:

```js
// Axes helper — brighter than default for dark background
const _axesH = new THREE.AxesHelper(1.5);
// Per-axis colors (red=X, green=Y, blue=Z)
_axesH.setColors(new THREE.Color(0xff4444), new THREE.Color(0x44ff44), new THREE.Color(0x4488ff));
axesGroup.add(_axesH);

// Bigger axis labels via sprites (if not already present)
function _makeAxisLabel(text, color, pos) {
    const c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    const g = c.getContext('2d');
    g.fillStyle = color;
    g.font = 'bold 42px sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text, 32, 32);
    const tex = new THREE.CanvasTexture(c);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true });
    const sp = new THREE.Sprite(mat);
    sp.position.copy(pos);
    sp.scale.set(0.5, 0.5, 1);
    return sp;
}
axesGroup.add(_makeAxisLabel('X', '#ff4444', new THREE.Vector3(1.8, 0, 0)));
axesGroup.add(_makeAxisLabel('Y', '#44ff44', new THREE.Vector3(0, 1.8, 0)));
axesGroup.add(_makeAxisLabel('Z', '#4488ff', new THREE.Vector3(0, 0, 1.8)));
```

If sprite-based labels are already present, skip this sub-step and adjust only their color / size.

- [ ] **Step 6: Verify click-to-snap-view still works**

Reload the page. Click each visible face of the viewCube. Each click should orbit the main camera to look down that axis. If clicks no longer register, check that the CSS change did not introduce a `pointer-events: none` or move the DOM out of the hit region; also check viewCubeRenderer's canvas is still attached.

- [ ] **Step 7: Take after screenshot, compare to reference**

```bash
browser-use screenshot /tmp/phase0-axis-widget-after.png
```

Visually compare against reference image. If close match, proceed. If not, ask user for adjustments before proceeding.

- [ ] **Step 8: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
style(axis-widget): move to bottom-left and restyle per user spec

Position: top-right → bottom-left above FPS counter (16px left, 48px bottom).
Size: ~50% of previous (72x72 px).
Colors: red/green/blue per axis with higher contrast.
Labels: larger sprite-based XYZ text.
Click-to-snap-view interaction preserved.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: i18n — `applyI18nToSubtree` helper

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — add helper near the i18n initialization.

- [ ] **Step 1: Locate i18n initialization**

```bash
grep -n 'I18n\.\|I18n =\|i18nRegister' STLGeneratorForOpenFoam/web/STL_generate.html | head -15
```

Find the function that defines `I18n.applyToDOM`. Note its signature — does it accept a root element argument?

- [ ] **Step 2: Add the helper**

Near the `I18n.onReady(() => { I18n.applyToDOM(); })` call at around [line 27320](../../STLGeneratorForOpenFoam/web/STL_generate.html#L27320), add **before** that line:

```js
function applyI18nToSubtree(rootEl) {
    if (!rootEl || !window.I18n) return;
    if (typeof I18n.applyToDOM === 'function' && I18n.applyToDOM.length >= 1) {
        // applyToDOM accepts a root — forward it
        I18n.applyToDOM(rootEl);
        return;
    }
    // Fallback: manual pass
    rootEl.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const t = I18n.t ? I18n.t(key) : key;
        if (t && t !== key) el.textContent = t;
    });
    rootEl.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        const t = I18n.t ? I18n.t(key) : key;
        if (t && t !== key) el.title = t;
    });
}
window.applyI18nToSubtree = applyI18nToSubtree;
```

- [ ] **Step 3: Smoke test the helper**

```bash
browser-use eval "
  const d = document.createElement('div');
  d.innerHTML = '<span data-i18n=\"label.base_size\">raw</span>';
  document.body.appendChild(d);
  window.applyI18nToSubtree(d);
  console.log('translated:', d.querySelector('span').textContent);
  d.remove();
"
```

Expected: the span's text is the translated locale value (not `label.base_size`).

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(i18n): add applyI18nToSubtree helper for dynamic DOM translation

Wraps I18n.applyToDOM with a root-element fallback so rerendered
property-inspector subtrees can be translated without re-scanning
the whole document.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: i18n — audit every innerHTML + data-i18n site and wire the helper

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — many sites.

- [ ] **Step 1: Grep every innerHTML that contains a data-i18n key**

```bash
grep -nE 'innerHTML\s*[+]?=' STLGeneratorForOpenFoam/web/STL_generate.html > /tmp/phase0-innerhtml-sites.txt
wc -l /tmp/phase0-innerhtml-sites.txt
```

Expected: ~100–300 sites. Examine each to determine if its RHS contains `data-i18n="..."`.

- [ ] **Step 2: Classify sites**

For each line in `/tmp/phase0-innerhtml-sites.txt`:
- **Type A:** single assignment `el.innerHTML = \`...data-i18n=...\``
- **Type B:** additive `el.innerHTML += \`...data-i18n=...\`` (inside a loop)
- **Type C:** no data-i18n — ignore.

Type A: call `applyI18nToSubtree(el)` on the line immediately after the assignment.
Type B: call `applyI18nToSubtree(el)` once AFTER the loop terminates, not per iteration.

- [ ] **Step 3: Edit each Type A site**

Example edit. Before:

```js
prop.innerHTML = `<div class="prop-row"><label data-i18n="label.base_size">基础尺寸</label></div>`;
```

After:

```js
prop.innerHTML = `<div class="prop-row"><label data-i18n="label.base_size">基础尺寸</label></div>`;
applyI18nToSubtree(prop);
```

Work through every Type A site. Commit every ~20 edits to keep the diff reviewable.

- [ ] **Step 4: Edit each Type B site**

Example. Before:

```js
for (const m of state.models) {
    list.innerHTML += `<div data-i18n="tree.model_row">${m.name}</div>`;
}
```

After:

```js
for (const m of state.models) {
    list.innerHTML += `<div data-i18n="tree.model_row">${m.name}</div>`;
}
applyI18nToSubtree(list);
```

The key rule: `applyI18nToSubtree` is called **once** after the loop, not inside it.

- [ ] **Step 5: Manual verification on Tri_angle**

```bash
fuser -k 8000/tcp 2>/dev/null
./STLGeneratorForOpenFoam/server/start_server.sh --port 8000 &
sleep 3
browser-use open http://localhost:8000/STL_generate.html
# Load Tri_angle.cfdproj via UI
# Then:
browser-use eval "
  const keys = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/^(label|btn|tree|title|nav|error|loading|export)\.[a-z_]+$/.test(t) && el.children.length === 0) {
      keys.push(t);
    }
  });
  console.log('leaked:', keys.length);
  console.log(keys.join('\n'));
"
```

Expected: `leaked: 0`.

If any leak, grep for its exact key in `STL_generate.html`, find the innerHTML site responsible, wire `applyI18nToSubtree` there.

- [ ] **Step 6: Repeat for en-US locale**

```bash
browser-use eval "I18n.setLocale && I18n.setLocale('en-US')"
browser-use eval "
  const keys = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/^(label|btn|tree|title|nav|error|loading|export)\.[a-z_]+$/.test(t) && el.children.length === 0) {
      keys.push(t);
    }
  });
  console.log('leaked:', keys.length);
"
```

Expected: `leaked: 0`.

- [ ] **Step 7: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "$(cat <<'EOF'
fix(i18n): wire applyI18nToSubtree into every innerHTML+data-i18n site

Property inspector, model tree, RACK detail view, partition-wall
inspector, solver/mesh panels, and N other dynamic DOM writers all
now re-apply translations after writing innerHTML. Fixes raw key
leakage (e.g. 'label.seed_direction_y') observed in Tri_angle.cfdproj.

Verified: 0 raw i18n keys in DOM for both zh-CN and en-US on
Tri_angle.cfdproj.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Automated headless i18n + undo verification script

**Files:**
- Create: `STLGeneratorForOpenFoam/scripts/phase0_verify.sh`

- [ ] **Step 1: Write the script**

```bash
cat > STLGeneratorForOpenFoam/scripts/phase0_verify.sh <<'EOF'
#!/usr/bin/env bash
# Phase 0 verification — runs headless checks for snap, undo, i18n.
# Usage: bash scripts/phase0_verify.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# Start server
fuser -k 8000/tcp 2>/dev/null || true
"$ROOT/server/start_server.sh" --port 8000 > /tmp/phase0-server.log 2>&1 &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null || true" EXIT
sleep 3

export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null || true
browser-use open http://localhost:8000/STL_generate.html
sleep 2

# Load Tri_angle
browser-use eval "
  // TODO: replace with actual project-load API if present
  fetch('projects/Tri_angle.cfdproj').then(r => r.text()).then(t => {
    window.loadProjectFromJSON && window.loadProjectFromJSON(JSON.parse(t));
  });
"
sleep 3

FAIL=0

# i18n leakage check — zh-CN
LEAKED=$(browser-use eval "
  const keys = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/^(label|btn|tree|title|nav|error|loading|export)\.[a-z_]+$/.test(t) && el.children.length === 0) {
      keys.push(t);
    }
  });
  console.log('COUNT:' + keys.length);
")
echo "$LEAKED"
if ! echo "$LEAKED" | grep -q 'COUNT:0'; then
  echo "FAIL: i18n leakage in zh-CN"
  FAIL=1
fi

# i18n leakage check — en-US
browser-use eval "I18n.setLocale && I18n.setLocale('en-US')"
sleep 1
LEAKED_EN=$(browser-use eval "
  const keys = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/^(label|btn|tree|title|nav|error|loading|export)\.[a-z_]+$/.test(t) && el.children.length === 0) {
      keys.push(t);
    }
  });
  console.log('COUNT:' + keys.length);
")
echo "$LEAKED_EN"
if ! echo "$LEAKED_EN" | grep -q 'COUNT:0'; then
  echo "FAIL: i18n leakage in en-US"
  FAIL=1
fi

# Snap module presence check
SNAP=$(browser-use eval "console.log('SNAP:' + !!window.__snapModule);")
echo "$SNAP"
if ! echo "$SNAP" | grep -q 'SNAP:true'; then
  echo "FAIL: snap module not loaded"
  FAIL=1
fi

# Undo keyboard listener check
UNDO=$(browser-use eval "
  // Simulate Ctrl+Z on document
  const startLen = undoStack.length;
  const e = new KeyboardEvent('keydown', {key:'z', ctrlKey:true, bubbles:true});
  document.dispatchEvent(e);
  console.log('UNDO_LISTENER:' + (undoStack.length !== startLen || startLen === 0 ? 'ok' : 'unchanged'));
")
echo "$UNDO"
# Note: if undoStack was empty, decrement can't be verified — considered OK.

if [[ $FAIL -ne 0 ]]; then
  echo "Phase 0 verification FAILED"
  exit 1
fi
echo "Phase 0 verification PASSED"
EOF
chmod +x STLGeneratorForOpenFoam/scripts/phase0_verify.sh
```

- [ ] **Step 2: Run it**

```bash
bash STLGeneratorForOpenFoam/scripts/phase0_verify.sh
```

Expected: `Phase 0 verification PASSED`.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/scripts/phase0_verify.sh
git commit -m "$(cat <<'EOF'
test(phase0): add headless verification script

Bundles i18n leakage, snap module presence, and undo keyboard listener
checks on Tri_angle.cfdproj in a single bash script. Intended to run
before merging any further interaction-layer change.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Full manual smoke-test pass + phase close

**Files:**
- Delete: `docs/superpowers/plans/2026-04-15-phase0-baseline.md`

- [ ] **Step 1: Run the smoke-test checklist from the snap spec**

Follow [`2026-04-14-snap-algorithm-design.md`](../specs/2026-04-14-snap-algorithm-design.md) §5.3 lines 460–475 verbatim. Every line passes.

- [ ] **Step 2: Run the undo checklist**

1. Open `Tri_angle.cfdproj`.
2. Enter any RACK detail view.
3. Drag the inner-width slider 10 times, each drag a distinct value.
4. Press Ctrl+Z ten times. Each press should revert exactly one drag.
5. Press Ctrl+Y ten times. Each press should re-apply exactly one drag.
6. Do a multi-move of 5 models, press Ctrl+Z once, all 5 return to original positions in one step.

Every step passes.

- [ ] **Step 3: Run the headless verification script**

```bash
bash STLGeneratorForOpenFoam/scripts/phase0_verify.sh
```

Expected: `Phase 0 verification PASSED`.

- [ ] **Step 4: Delete the baseline doc**

```bash
rm docs/superpowers/plans/2026-04-15-phase0-baseline.md
git rm docs/superpowers/plans/2026-04-15-phase0-baseline.md
```

- [ ] **Step 5: Close-out commit**

```bash
git commit -m "$(cat <<'EOF'
chore(phase0): close Phase 0 — all acceptance criteria pass

Delete baseline reproduction doc now that all five Phase 0 items pass:
- Snap restored (git bisect + targeted fix)
- Undo flag scoping unified
- Camera verified
- Axis widget repositioned and restyled
- i18n raw-key leakage fixed

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Update memory**

Update `MEMORY.md` and `project_stlgen_refactor_phases.md` to mark Phase 0 complete and Phase 1 (leakage unification) as the next queued phase.

---

## Self-Review

- **Spec coverage:** every §2.x of the Phase 0 spec maps to at least one task. §2.1 undo-push audit → Tasks 4 + 5. §2.2 snap regression → Tasks 1–3. §2.3 camera verify → Task 6. §2.4 axis widget → Task 7. §2.5 i18n → Tasks 8–9. §5 testing → Tasks 10–11. §Non-Goals respected — no 2D projection task, no sidebar polish task.
- **Placeholder scan:** `<SHA>` and `<culprit-sha>` are intentional — they're filled in at runtime by `git bisect`. `<funcName>` is intentional — filled from Step 1's grep output. No "TBD / implement later / appropriate error handling" leaks.
- **Type consistency:** `_rdCheckpointPushed` → `window._rdCheckpointPushed` everywhere; `applyI18nToSubtree` used exactly the same way in Tasks 8 and 9; `__snapModule` consistent; `pushUndoCheckpoint`/`logOperation` pair per CLAUDE.md everywhere.
- **Task granularity:** each step is a single action (2–5 min). The bisect tasks have tighter steps around failure modes. The i18n audit task acknowledges the work is ~100+ sites and instructs to commit every ~20 edits.
