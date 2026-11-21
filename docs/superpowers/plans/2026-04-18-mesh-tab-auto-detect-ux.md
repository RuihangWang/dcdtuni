# Mesh Tab Auto-Detect + Postprocess Refresh UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three tightly-related frontend UX fixes in `STLGeneratorForOpenFoam/web/STL_generate.html`: (1) postprocess tab button becomes disabled when refresh finds no results, (2) auto refinement-detect runs only on first mesh-tab entry per edit session, (3) flag resets on any refinement-level edit.

**Architecture:** Pure frontend, single monolithic HTML. One in-memory `window._autoDetectDone` flag gates `switchLeftTab('mesh')` → `runAutoDetectRackRefinement()`. Both auto-detect functions set it `true` at end; `setMeshRefineLevel` + `_applyProjectData` set it `false`. Feature 1 tightens `scanCaseStatus` to set `solveComplete = !!data.postprocessReady` deterministically (not just on truthy).

**Tech Stack:** Vanilla JS + Three.js (monolith HTML), Python regex-scan structural tests (pattern from `test_sensor_points_frontend.py`).

**Spec:** `docs/superpowers/specs/2026-04-18-mesh-tab-auto-detect-ux-design.md`

---

## File Structure

**Create:**
- `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py` — structural regex-scan tests (6 tests)

**Modify:**
- `STLGeneratorForOpenFoam/web/STL_generate.html` — 5 surgical edits:
  1. Add `window._autoDetectDone = false;` global init near other UX globals
  2. `scanCaseStatus`: deterministic `solveComplete = !!data.postprocessReady`
  3. `switchLeftTab('mesh')` branch: wrap call with `!window._autoDetectDone` gate
  4. `runAutoDetectRefinement` + `runAutoDetectRackRefinement`: append `window._autoDetectDone = true;`
  5. `setMeshRefineLevel` + `_applyProjectData`: append `window._autoDetectDone = false;`

Run tests via: `conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v`.

All commits land in the **submodule** (`STLGeneratorForOpenFoam`); Task 6 bumps the parent repo submodule pointer.

---

## Task Breakdown

---

### Task 1: Test file scaffold + global `_autoDetectDone` init

**Files:**
- Create: `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (1 line added near other window UX globals)

- [ ] **Step 1: Create test file with failing tests**

Create `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`:

```python
"""Structural tests for mesh-tab auto-detect + postprocess refresh UX.

Spec: docs/superpowers/specs/2026-04-18-mesh-tab-auto-detect-ux-design.md

Regex-scans STL_generate.html for the existence of required window globals,
function edits, and wiring. Style mirrors test_sensor_points_frontend.py.
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "STL_generate.html"
HTML = HTML_PATH.read_text(encoding="utf-8")


def extract_fn_body(name):
    m = re.search(rf"function {re.escape(name)}\s*\([^)]*\)\s*\{{", HTML)
    if not m:
        m = re.search(
            rf"window\.{re.escape(name)}\s*=\s*(?:async\s+)?function\s*\([^)]*\)\s*\{{",
            HTML,
        )
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


def test_autoDetectDone_global_declaration():
    """window._autoDetectDone must be initialized to false at script load."""
    assert re.search(r"window\._autoDetectDone\s*=\s*false", HTML), \
        "window._autoDetectDone = false; must be declared"
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -10
```

Expected: `test_autoDetectDone_global_declaration` FAILS with `assert None`.

- [ ] **Step 3: Add global init in STL_generate.html**

Find the nearby UX global `window._lastSensorJsonPath = null;` (added in the sensor-points feature). Directly after it, add:

```js
    window._autoDetectDone = false;  // gates switchLeftTab('mesh') auto-detect; reset by setMeshRefineLevel / _applyProjectData
```

- [ ] **Step 4: Verify test passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -5
```

Expected: 1 passed.

- [ ] **Step 5: Commit in submodule**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_mesh_tab_auto_detect_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): add window._autoDetectDone flag init + test scaffold

In-memory gate for mesh-tab auto refinement-detect. Reset by
setMeshRefineLevel / _applyProjectData (future tasks); set by the auto-
detect fns. See spec 2026-04-18-mesh-tab-auto-detect-ux-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `scanCaseStatus` — deterministic `solveComplete` assignment (Feature 1)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (function `scanCaseStatus` ~L19460)
- Test: `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`

- [ ] **Step 1: Add failing test**

Append to `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`:

```python
def test_scancasestatus_sets_solvecomplete_deterministically():
    """scanCaseStatus must assign solveComplete from data.postprocessReady
    unconditionally (two-way sync), not only on truthy branch."""
    body = extract_fn_body("scanCaseStatus")
    assert body is not None, "window.scanCaseStatus must exist"
    # Canonical form: solveComplete = !!data.postprocessReady
    # Accept variants with Boolean(data.postprocessReady) / === true etc.
    has_deterministic = re.search(
        r"solveComplete\s*=\s*!!\s*data\.postprocessReady", body
    ) or re.search(
        r"solveComplete\s*=\s*Boolean\(\s*data\.postprocessReady\s*\)", body
    )
    assert has_deterministic, (
        "scanCaseStatus must deterministically set "
        "state.dctwinConfig.solveComplete = !!data.postprocessReady"
    )
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -10
```

Expected: `test_scancasestatus_sets_solvecomplete_deterministically` FAILS.

- [ ] **Step 3: Modify `scanCaseStatus`**

Locate this block in `STL_generate.html` (~L19476-L19483):

```js
            if (data.postprocessReady) {
                state.dctwinConfig.solveComplete = true;
                const ppBtn = document.getElementById('tab-btn-postprocess');
                if (ppBtn) { ppBtn.disabled = false; ppBtn.title = ''; }
                const initBtn = document.getElementById('btn-init-postprocess');
                if (initBtn) initBtn.disabled = false;
                logOperation('scanCaseStatus', t('log.postprocess_data_ready'), {}, 'file');
            }
```

Replace with:

```js
            // 2026-04-18: two-way sync. solveComplete tracks data.postprocessReady
            // unconditionally so _updatePostprocessTabEnabled() (called at the end
            // of this function) disables the button when results disappear.
            state.dctwinConfig.solveComplete = !!data.postprocessReady;
            if (data.postprocessReady) {
                const ppBtn = document.getElementById('tab-btn-postprocess');
                if (ppBtn) { ppBtn.disabled = false; ppBtn.title = ''; }
                const initBtn = document.getElementById('btn-init-postprocess');
                if (initBtn) initBtn.disabled = false;
                logOperation('scanCaseStatus', t('log.postprocess_data_ready'), {}, 'file');
            }
```

- [ ] **Step 4: Verify test passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -5
```

Expected: 2 passed.

- [ ] **Step 5: Commit in submodule**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_mesh_tab_auto_detect_frontend.py
git commit -m "$(cat <<'EOF'
fix(ui): scanCaseStatus sets solveComplete deterministically (two-way)

Previously solveComplete was only set to true when data.postprocessReady
was truthy, leaving stale true when results were deleted externally.
Now assigns !!data.postprocessReady unconditionally so
_updatePostprocessTabEnabled() disables the button symmetrically.

Feature 1 of spec 2026-04-18-mesh-tab-auto-detect-ux-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `switchLeftTab('mesh')` — gate auto-detect with `_autoDetectDone` (Feature 2)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (function `switchLeftTab` ~L19568)
- Test: `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`

- [ ] **Step 1: Add failing test**

Append:

```python
def test_switchlefttab_mesh_gated_by_autoDetectDone():
    """switchLeftTab's mesh branch must not unconditionally run
    runAutoDetectRackRefinement; it must be gated by !_autoDetectDone."""
    body = extract_fn_body("switchLeftTab")
    assert body is not None, "window.switchLeftTab must exist"
    # Isolate the mesh-tab branch: line(s) that mention 'mesh' + the auto-detect call
    mesh_region = re.search(
        r"tab\s*===\s*['\"]mesh['\"][\s\S]{0,500}?runAutoDetectRackRefinement",
        body,
    )
    assert mesh_region is not None, \
        "mesh branch must still call runAutoDetectRackRefinement (somewhere)"
    region = mesh_region.group(0)
    assert "_autoDetectDone" in region, \
        "mesh branch must reference _autoDetectDone before calling the auto-detect fn"
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -8
```

Expected: `test_switchlefttab_mesh_gated_by_autoDetectDone` FAILS.

- [ ] **Step 3: Modify `switchLeftTab`**

Locate in `STL_generate.html` around L19575:

```js
    if (tab === 'mesh') {
        window.runAutoDetectRackRefinement();
    }
```

Replace with:

```js
    if (tab === 'mesh') {
        // 2026-04-18: gate on _autoDetectDone so repeated tab-switches without
        // refinement edits don't re-run the detect. runAutoDetectRackRefinement
        // sets _autoDetectDone = true at its tail; setMeshRefineLevel /
        // _applyProjectData flip it back to false.
        if (!window._autoDetectDone) {
            window.runAutoDetectRackRefinement();
        }
    }
```

- [ ] **Step 4: Verify test passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -5
```

Expected: 3 passed.

- [ ] **Step 5: Commit in submodule**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_mesh_tab_auto_detect_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): switchLeftTab('mesh') gates auto-detect behind _autoDetectDone

Previously every mesh-tab entry triggered runAutoDetectRackRefinement,
spamming the log and recomputing levels unnecessarily. Now the call is
wrapped in !window._autoDetectDone; the trailing assignment inside the
auto-detect fn (next task) flips the flag true, while user refinement
edits flip it back false (later task).

Feature 2 of spec 2026-04-18-mesh-tab-auto-detect-ux-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Auto-detect fns — set `_autoDetectDone = true` at end (Feature 2 follow-up)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (functions `runAutoDetectRefinement` ~L25901 and `runAutoDetectRackRefinement` ~L25849)
- Test: `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`

- [ ] **Step 1: Add failing test**

Append:

```python
def test_auto_detect_fns_set_done_true():
    """Both runAutoDetectRefinement (manual button) and
    runAutoDetectRackRefinement (tab-switch variant) must set
    _autoDetectDone = true at completion."""
    for fn in ("runAutoDetectRefinement", "runAutoDetectRackRefinement"):
        body = extract_fn_body(fn)
        assert body is not None, f"{fn} must exist"
        assert re.search(r"_autoDetectDone\s*=\s*true", body), \
            f"{fn} must set window._autoDetectDone = true at end"
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -8
```

Expected: `test_auto_detect_fns_set_done_true` FAILS (both branches).

- [ ] **Step 3: Append to both auto-detect fns**

Grep for `function runAutoDetectRefinement` and `function runAutoDetectRackRefinement` (or `window.runAutoDetectRefinement = function` / `window.runAutoDetectRackRefinement = function` — either form works, the skill's declaration pattern may differ; check both).

At the LAST statement inside each function body (just before the closing `};` or `}`), add:

```js
        window._autoDetectDone = true;
```

For `runAutoDetectRefinement`, the tail looks like (find by grepping for the function name, then walk to the closing brace):

```js
    window.runAutoDetectRefinement = function() {
        // ... existing body ...
        // (existing trailing lines, likely a logOperation)
        window._autoDetectDone = true;  // <-- ADD THIS
    };
```

Same for `runAutoDetectRackRefinement`. If either function internally delegates to an async call (e.g. `.then(...)`), put the flag set inside the resolve/finally callback — but inspect first; per existing code reading, both functions are synchronous.

- [ ] **Step 4: Verify test passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -5
```

Expected: 4 passed.

- [ ] **Step 5: Commit in submodule**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_mesh_tab_auto_detect_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): auto-detect fns set _autoDetectDone = true on completion

Both runAutoDetectRefinement (full, manual button) and
runAutoDetectRackRefinement (rack-scoped, tab-switch) set the flag after
running, so the next repeated mesh-tab entry short-circuits until the
user edits a refinement level.

Feature 2 of spec 2026-04-18-mesh-tab-auto-detect-ux-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `setMeshRefineLevel` + `_applyProjectData` — clear `_autoDetectDone = false` (Feature 3)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (functions `setMeshRefineLevel` ~L26066 and `_applyProjectData` ~L15134)
- Test: `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_setmeshrefinelevel_clears_autoDetectDone():
    """Any user refinement-level edit (all flow through setMeshRefineLevel)
    must clear _autoDetectDone so next mesh-tab entry re-detects."""
    body = extract_fn_body("setMeshRefineLevel")
    assert body is not None, "window.setMeshRefineLevel must exist"
    assert re.search(r"_autoDetectDone\s*=\s*false", body), \
        "setMeshRefineLevel must set window._autoDetectDone = false"


def test_applyprojectdata_resets_autoDetectDone():
    """Loading a project resets the flag so a fresh auto-detect runs on
    the first mesh-tab visit for the new project."""
    body = extract_fn_body("_applyProjectData")
    assert body is not None, "window._applyProjectData must exist"
    assert re.search(r"_autoDetectDone\s*=\s*false", body), \
        "_applyProjectData must reset window._autoDetectDone = false"
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -10
```

Expected: 2 new tests FAIL.

- [ ] **Step 3: Append to `setMeshRefineLevel`**

Grep for `window.setMeshRefineLevel = function` (~L26066). At the tail of the function body, before the closing `};`, add:

```js
        window._autoDetectDone = false;
```

- [ ] **Step 4: Append to `_applyProjectData`**

Grep for `window._applyProjectData = function` (~L15134). At the tail of the function body, before the closing `};`, add:

```js
        window._autoDetectDone = false;
```

Place it near the other per-load UX resets (e.g. alongside `redoStack.length = 0;` around L15159, or grouped with the `window._solverMonitor = {...}` reset around L15143).

- [ ] **Step 5: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py -v 2>&1 | tail -5
```

Expected: 6 passed.

- [ ] **Step 6: Commit in submodule**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_mesh_tab_auto_detect_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): clear _autoDetectDone on refinement edits + project load

setMeshRefineLevel is the single funnel for all surface/feature level
mutations (report confirms all ms-rl-* / ms-rack-* / ms-acu-* / ms-box-*
/ ms-pw-* / ms-bfl-* / ms-feat-* and per-unit category setters flow
through it). Flipping the flag here guarantees the next mesh-tab entry
recomputes.

_applyProjectData resets on project load so new cfdproj gets a fresh
first-visit detect.

Feature 3 of spec 2026-04-18-mesh-tab-auto-detect-ux-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Regression sweep + parent-repo submodule bump

**Files:**
- No file changes in submodule — just run the full regression suite.
- Parent repo: `git add STLGeneratorForOpenFoam && git commit ...` to bump submodule pointer.

- [ ] **Step 1: Full regression sweep**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest \
    STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py \
    STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py \
    STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py \
    STLGeneratorForOpenFoam/scripts/test_refinement_redesign_runtime.py \
    STLGeneratorForOpenFoam/server/tests/test_probes_api.py \
    src/backend/aircooling/runner/tests/test_refinement_redesign.py \
    src/backend/aircooling/runner/tests/test_rack_door_refinement.py \
    -q 2>&1 | tail -5
```

Expected: 109+ passed (104 pre-existing + 6 new sensor-points + 6 new mesh-tab-auto-detect).

Note: sensor-points adds 28 frontend + 15 server = 43 total from prior session; refinement adds 40 frontend + ~25 runtime + 25 runner + 1 rack door = ~91; mesh-tab-auto-detect adds 6. Final target: ~109+6 = **~115 passed**.

- [ ] **Step 2: Submodule state check**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && git status --short STLGeneratorForOpenFoam && git -C STLGeneratorForOpenFoam log --oneline -5
```

Expected: `M STLGeneratorForOpenFoam`; top 5 submodule commits cover Tasks 1–5.

- [ ] **Step 3: Parent-repo submodule bump commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && git add STLGeneratorForOpenFoam && git commit -m "$(cat <<'EOF'
chore(submodule): bump STLGen — mesh-tab auto-detect + postprocess refresh UX

Picks up 5 submodule commits implementing spec
2026-04-18-mesh-tab-auto-detect-ux-design.md:
- Feature 1: scanCaseStatus deterministic solveComplete (two-way sync)
- Feature 2: switchLeftTab('mesh') gated by window._autoDetectDone
- Feature 3: setMeshRefineLevel / _applyProjectData clear the flag;
  auto-detect fns set it

6 new frontend structural regression tests (~115 total in sweep).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task Dependency Graph

```
T1 (global init + test file)
  └─→ T2 (Feature 1: scanCaseStatus)
         └─→ T3 (switchLeftTab gate)
                └─→ T4 (auto-detect fns set done=true)
                       └─→ T5 (clear done=false on edits / load)
                              └─→ T6 (regression + bump)
```

Serial execution. Each task's commit can stand on its own (no broken intermediate states, though T3+T4 together make up the Feature-2 behavior — running T3 alone would never set the flag back true → tab-switch auto-detect runs once then never again; T4 completes the loop).
