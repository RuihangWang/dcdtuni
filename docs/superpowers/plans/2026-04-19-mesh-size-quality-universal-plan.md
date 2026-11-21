# Universal Mesh Size + Quality Defaults — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three coordinated changes from `docs/superpowers/specs/2026-04-19-mesh-size-quality-universal-design.md` — baseSize default to 0.35, tightened snappy quality params, and a new `meshDetectBaseSize` decoupling field + UI.

**Architecture:** Change 1 is already executed in the working tree (code + data edits uncommitted) — Task 1 just commits it. Change 2 edits the f-string template inside `stlgen_runner._generate_snappy_hex_mesh_dict` (the real emitter — the Jinja2 files referenced in the spec are unused by this code path). Change 3 adds a single `detectBaseSize` field to `MeshConfig`, a sibling `meshDetectBaseSize` key in frontend `cfdRoom` state, one UI row with a number input + ✖ clear button, and swaps five JS callsites from `state.cfdRoom.meshBaseSize` to a `_getDetectBaseSize()` helper.

**Tech Stack:** Python 3.10 (`conda activate dcdtuni`) + Pydantic v2 for schema, vanilla JS + THREE.js for frontend, Jinja-less f-string template for `snappyHexMeshDict`, pytest for unit tests, browser-use (`~/.local/bin/browser-use`) for UI verification.

**Runtime Facts (verified 2026-04-19):**
- `_generate_snappy_hex_mesh_dict` is at `src/backend/aircooling/runner/stlgen_runner.py:3486-4401`; the f-string body starts at line 4280 and hard-codes the 4 params we're changing at lines **4303** (`nCellsBetweenLevels 3`), **4336** (`nFeatureSnapIter 10`), **4366** (`maxNonOrtho 65`), **4389** (`maxNonOrtho 75` inside `relaxed`).
- Five JS sites read `state.cfdRoom.meshBaseSize` for auto-detect math: lines **25981** (`_autoDetectCategory`), **26061** (`_autoDetectRackFrame`), **26203** (`_collectCategoryWarnings`), **26330**, **26387** (both use the same fallback pattern inside warning collectors).
- cfdrun export is at line **29183** (`baseSize: state.cfdRoom.meshBaseSize`) — the new field goes on the line right after.
- Auto-detect button is `STL_generate.html:162`; we add the detect-base input as an independent `.prop-row` immediately before that button (same prop-group).

---

## File Structure

**Modified (Change 2 — runner-side):**
- `src/backend/aircooling/runner/stlgen_runner.py` — 4 integer edits inside the f-string template (lines 4303, 4336, 4366, 4389)
- `src/backend/aircooling/runner/tests/test_snappy_quality_defaults.py` (new) — 1 parameterised test asserting the 4 new values appear verbatim in the rendered dict

**Modified (Change 3 — schema):**
- `src/backend/aircooling/runner/cfdproj_schema.py` — add `detectBaseSize: Optional[float] = None` field (and `model_dump(exclude_none=True)` is default for None)
- `src/backend/aircooling/runner/tests/test_mesh_detect_base_size_roundtrip.py` (new) — round-trip tests for None vs non-None

**Modified (Change 3 — frontend):**
- `STLGeneratorForOpenFoam/web/STL_generate.html`:
  - State init (line 2906 area) — add `meshDetectBaseSize: null` sibling
  - Mesh settings panel (before line 162) — add 1 `.prop-row` with number input + ✖ button
  - UI sync (`set('ms-base-size', cr.meshBaseSize)` at line 25655) — mirror sync for detect input
  - 5 auto-detect sites (25981, 26061, 26203, 26330, 26387) — replace `state.cfdRoom.meshBaseSize || 0.4` with `_getDetectBaseSize()`
  - Helper `_getDetectBaseSize()` added in the same scope as the auto-detect fns (just above `computeMinSafeLevel` at ~25968)
  - cfdrun export (line 29183 area) — add `detectBaseSize` when `meshDetectBaseSize` truthy

**Modified (Task 1 — commit Change 1):**
No new edits — just commits the already-done work:
- Outer repo: `src/backend/aircooling/runner/cfdproj_schema.py`, `src/backend/aircooling/runner/stlgen_runner.py`, `AGENTS.md`, 7 cfdrun files under `case/*/model/`
- STLGen submodule: `STLGeneratorForOpenFoam/web/STL_generate.html`, `STLGeneratorForOpenFoam/Instruction.md`, 14 cfdproj files under `STLGeneratorForOpenFoam/projects/`

---

## Task 1: Commit Change 1 (already-done baseSize=0.35 work)

**Files:**
- Outer repo: working-tree modifications from earlier in this session
- Submodule `STLGeneratorForOpenFoam`: same

**Pre-flight check — the working tree must already contain the 0.35 changes.** If `baseSize: float = 0.2` still appears in `cfdproj_schema.py:403`, the session was reset; re-run the earlier batch edits before this task.

- [ ] **Step 1: Verify Change 1 is staged correctly in both repos**

Run:
```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni status --short
git -C /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam status --short
```

Expected in outer repo (≈11 entries): `M AGENTS.md`, `M STLGeneratorForOpenFoam` (submodule ptr), `M src/backend/aircooling/runner/cfdproj_schema.py`, `M src/backend/aircooling/runner/stlgen_runner.py`, plus 7 `M case/*/model/*.cfdrun`.

Expected in submodule (≈16 entries): `M Instruction.md`, `M web/STL_generate.html`, plus 14 `M projects/*.cfdproj`.

If files are missing, stop and escalate (session state is inconsistent with the plan).

- [ ] **Step 2: Verify tests pass before committing**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ --ignore=src/backend/aircooling/runner/tests/test_partition_hole_fvoptions.py -q
```

Expected: `138 passed` (the ignored test is a pre-existing failure unrelated to this plan).

- [ ] **Step 3: Commit the submodule (data + HTML + docs)**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html Instruction.md projects/*.cfdproj
git commit -m "$(cat <<'EOF'
refactor(mesh): default baseSize 0.2/0.25/0.4 → 0.35 (8U-equivalent)

Normalizes every cfdproj in projects/ to baseSize=0.35. Also updates the
frontend state init + fallback reads and the example in Instruction.md.

0.35 is chosen because the log2 bucket it lands in for every standard U-span
patch (2U/4U/8U) coincides with 0.178–0.356m range; 0.4 was a trap —
just over the boundary, inflating every auto-detect level by 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit created, working tree clean in submodule.

- [ ] **Step 4: Commit the outer repo (runner defaults + cfdrun data + submodule bump)**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam src/backend/aircooling/runner/cfdproj_schema.py src/backend/aircooling/runner/stlgen_runner.py AGENTS.md case/*/model/*.cfdrun
git commit -m "$(cat <<'EOF'
refactor(mesh): default baseSize 0.2/0.25/0.4 → 0.35 (code + data)

- MeshConfig.baseSize default 0.2 → 0.35 in cfdproj_schema.py
- Three stlgen_runner.py fallback reads bumped
- 7 cfdrun files under case/*/model/ normalized to 0.35
- Submodule bump: 14 cfdproj normalized + frontend state init

Verification: 138 runner tests pass (test_partition_hole_fvoptions is a
pre-existing failure, unrelated).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit created, outer repo working tree clean.

---

## Task 2: Tighten snappy quality params (TDD)

**Files:**
- Create: `src/backend/aircooling/runner/tests/test_snappy_quality_defaults.py`
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:4303, 4336, 4366, 4389`

- [ ] **Step 1: Write the failing test**

Create `src/backend/aircooling/runner/tests/test_snappy_quality_defaults.py` with this content:

```python
"""DC-tuned snappy quality defaults (2026-04-19).

The 4 params below are hard-coded inside
``STLGenRunner._generate_snappy_hex_mesh_dict``'s f-string template at
stlgen_runner.py:4280-4399 (lines 4303/4336/4366/4389). They are NOT
fed through the Jinja template in third_parties/foam/template/mesh/ —
that template is dead code for this pipeline.

Rationale per spec 2026-04-19-mesh-size-quality-universal-design.md §Change 2:
  maxNonOrtho 65 → 60:   tighter non-orthogonality for buoyant solver
  maxNonOrtho 75 → 70:   same, relaxed-pass threshold
  nCellsBetweenLevels 3 → 4:  smoother level transitions, kills frame
                              distortion at room→door boundaries
  nFeatureSnapIter 10 → 15:   complex DC feature collisions need more iters
"""
from pathlib import Path

from .test_refinement_redesign import _bootstrap_runner, _write_dummy_stl


def test_snappy_quality_defaults_dc_tuned(tmp_path):
    """Render a minimal snappyHexMeshDict and assert the 4 DC-tuned
    quality params appear verbatim."""
    stl_solids = {"merged_box.stl": ["Merged_Box"]}
    mesh_config = {"baseSize": 0.35}
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)

    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)

    text = (system_dir / "snappyHexMeshDict").read_text()

    # castellatedMeshControls
    assert "nCellsBetweenLevels 4;" in text, \
        "nCellsBetweenLevels should be 4 (smoother level transitions)"

    # snapControls
    assert "nFeatureSnapIter 15;" in text, \
        "nFeatureSnapIter should be 15 (complex DC feature collisions)"

    # meshQualityControls (top-level and relaxed)
    assert "maxNonOrtho 60;" in text, \
        "top-level maxNonOrtho should be 60 (buoyant solver tolerance)"
    assert "maxNonOrtho 70;" in text, \
        "relaxed maxNonOrtho should be 70 (paired with top-level 60)"

    # Sanity: the old values should NOT survive anywhere in the dict.
    assert "nCellsBetweenLevels 3;" not in text
    assert "nFeatureSnapIter 10;" not in text
    assert "maxNonOrtho 65;" not in text
    assert "maxNonOrtho 75;" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_snappy_quality_defaults.py -v
```

Expected: FAIL with `AssertionError: nCellsBetweenLevels should be 4 (smoother level transitions)` — the first assertion fails because the current value is 3.

- [ ] **Step 3: Edit `nCellsBetweenLevels` 3 → 4**

In `src/backend/aircooling/runner/stlgen_runner.py` find the f-string body of `_generate_snappy_hex_mesh_dict` and change line 4303:

Old (exact single line):
```python
    nCellsBetweenLevels 3;
```

New:
```python
    nCellsBetweenLevels 4;
```

(Use `Edit` tool with `old_string="    nCellsBetweenLevels 3;"`, `new_string="    nCellsBetweenLevels 4;"` — the leading 4-space indent is inside the f-string and must be preserved.)

- [ ] **Step 4: Edit `nFeatureSnapIter` 10 → 15**

In the same file change line 4336:

Old:
```python
    nFeatureSnapIter 10;
```

New:
```python
    nFeatureSnapIter 15;
```

- [ ] **Step 5: Edit top-level `maxNonOrtho` 65 → 60**

Change line 4366:

Old:
```python
    maxNonOrtho 65;
```

New:
```python
    maxNonOrtho 60;
```

**Careful:** there are TWO `maxNonOrtho` lines in the f-string — this one is the top-level one (line 4366, after `meshQualityControls {`). Do NOT replace both with one call.

- [ ] **Step 6: Edit relaxed `maxNonOrtho` 75 → 70**

Change line 4389 (inside the `relaxed { ... }` block):

Old:
```python
        maxNonOrtho 75;
```

New:
```python
        maxNonOrtho 70;
```

(Note the 8-space indent — this line is inside a nested block.)

- [ ] **Step 7: Run the new test — should pass**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_snappy_quality_defaults.py -v
```

Expected: `1 passed`.

- [ ] **Step 8: Run the full suite — no regressions**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ --ignore=src/backend/aircooling/runner/tests/test_partition_hole_fvoptions.py -q
```

Expected: `139 passed` (138 before + the new one).

- [ ] **Step 9: Commit**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_snappy_quality_defaults.py
git commit -m "$(cat <<'EOF'
feat(mesh): tighten snappy quality params for DC buoyant solver

Four param changes inside _generate_snappy_hex_mesh_dict's f-string:
  maxNonOrtho 65 → 60     (buoyant solver sensitivity)
  maxNonOrtho 75 → 70     (relaxed-pass threshold)
  nCellsBetweenLevels 3 → 4   (kills room→door frame distortion)
  nFeatureSnapIter 10 → 15    (rack/door/perf feature collisions)

Kept at OF default: explicitFeatureSnap / implicitFeatureSnap /
resolveFeatureAngle / meshQualityControls min* thresholds.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `MeshConfig.detectBaseSize` schema field (TDD)

**Files:**
- Create: `src/backend/aircooling/runner/tests/test_mesh_detect_base_size_roundtrip.py`
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py` (add field near `baseSize` in `MeshConfig`)

- [ ] **Step 1: Write the failing test**

Create `src/backend/aircooling/runner/tests/test_mesh_detect_base_size_roundtrip.py`:

```python
"""MeshConfig.detectBaseSize — schema round-trip (2026-04-19).

Per spec 2026-04-19-mesh-size-quality-universal-design.md §Change 3:
- Defaults to None (backward compatible — existing cfdproj/cfdrun lack
  this field, auto-detect falls back to baseSize).
- When None, JSON dump must omit the field entirely (keeps existing
  files diff-clean on re-save).
- When set, round-trips as a plain float.

The runner itself does not consume this value (auto-detect is
client-side) but schema validation must accept + preserve it.
"""
import pytest

from dcdtuni.runner.cfdproj_schema import MeshConfig


def test_detect_base_size_defaults_to_none():
    """Brand-new MeshConfig has detectBaseSize == None."""
    mc = MeshConfig()
    assert mc.detectBaseSize is None


def test_detect_base_size_none_omitted_from_dump():
    """None → JSON dump omits the key (use exclude_none=True)."""
    mc = MeshConfig(baseSize=0.35)
    dumped = mc.model_dump(mode="json", exclude_none=True)
    assert "detectBaseSize" not in dumped
    # Sanity — the explicit field we set IS present
    assert dumped["baseSize"] == 0.35


def test_detect_base_size_set_roundtrips():
    """Non-None survives validate → dump cycle as a float."""
    raw = {"baseSize": 0.35, "detectBaseSize": 0.178}
    mc = MeshConfig.model_validate(raw)
    assert mc.detectBaseSize == pytest.approx(0.178)
    dumped = mc.model_dump(mode="json")
    assert dumped["detectBaseSize"] == pytest.approx(0.178)


def test_detect_base_size_accepts_float_strings_like_other_floats():
    """Pydantic coerces numeric strings → float (match baseSize behaviour)."""
    raw = {"baseSize": 0.35, "detectBaseSize": "0.178"}
    mc = MeshConfig.model_validate(raw)
    assert mc.detectBaseSize == pytest.approx(0.178)


def test_detect_base_size_explicit_null_stays_none():
    """Explicit JSON `null` → None (not a validation error)."""
    raw = {"baseSize": 0.35, "detectBaseSize": None}
    mc = MeshConfig.model_validate(raw)
    assert mc.detectBaseSize is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_mesh_detect_base_size_roundtrip.py -v
```

Expected: 5 FAILS, all with `AttributeError: 'MeshConfig' object has no attribute 'detectBaseSize'` or pydantic `ValidationError: extra inputs are not permitted` — depends on pydantic config.

- [ ] **Step 3: Add `detectBaseSize` field to `MeshConfig`**

In `src/backend/aircooling/runner/cfdproj_schema.py`, the `MeshConfig` class starts at line 402. Find the `baseSize: float = 0.35` line and add the new field directly after it.

Old:
```python
class MeshConfig(CFDBaseModel):
    baseSize: float = 0.35
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
```

New:
```python
class MeshConfig(CFDBaseModel):
    baseSize: float = 0.35
    # Auto-detect reference scale (2026-04-19 spec). When None, auto-detect
    # uses baseSize (backward compatible). When set, the client-side
    # ceil(log2(X / (H/2))) uses this value — decouples level-picking from
    # actual mesh baseSize so the user can pick a finer background for
    # solver stability without inflating auto-detect levels.
    detectBaseSize: Optional[float] = None
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
```

Verify `Optional` is already imported at the top of the file. Search for `from typing import`:
```bash
grep -n "^from typing" src/backend/aircooling/runner/cfdproj_schema.py
```

If `Optional` isn't listed, add it (typically `from typing import List, Dict, Optional`). Most likely it's already there — this schema file uses `Optional` elsewhere.

- [ ] **Step 4: Run the test — should pass**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_mesh_detect_base_size_roundtrip.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Run the full suite — no regressions**

Run:
```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ --ignore=src/backend/aircooling/runner/tests/test_partition_hole_fvoptions.py -q
```

Expected: `144 passed` (139 from previous tasks + 5 new).

- [ ] **Step 6: Commit**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/aircooling/runner/tests/test_mesh_detect_base_size_roundtrip.py
git commit -m "$(cat <<'EOF'
feat(schema): add MeshConfig.detectBaseSize for auto-detect decoupling

Optional[float] = None. When set, the frontend's auto-detect formula
ceil(log2(X / (patchHeight/2))) uses this X instead of baseSize.
Actual mesh cell size is still baseSize/2^L — this field only affects
which level numbers the client-side auto-detect picks.

Backward compatible: None → omitted from model_dump(exclude_none=True),
existing cfdproj/cfdrun lack the field → behave exactly as before.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend `_getDetectBaseSize()` helper + swap 5 auto-detect sites

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (helper + 5 site edits + 1 state init line)

This task is pure JS in the STLGen submodule. No Python tests. Verification is via browser-use in Task 7.

- [ ] **Step 1: Add `meshDetectBaseSize: null` to state initialization**

Find `STL_generate.html:2906` (the existing `meshBaseSize: 0.35,` line inside `state.cfdRoom`). Add a sibling on the next line.

Old:
```javascript
                meshBaseSize: 0.35,
```

New:
```javascript
                meshBaseSize: 0.35,
                meshDetectBaseSize: null,  // null → inherit meshBaseSize (see _getDetectBaseSize)
```

- [ ] **Step 2: Add `_getDetectBaseSize()` helper just above `computeMinSafeLevel`**

`computeMinSafeLevel` is at `STL_generate.html:25969`. Insert the helper directly before it.

Find this exact block:
```javascript
    function computeMinSafeLevel(baseSize, patchHeight) {
        if (patchHeight <= 0 || baseSize <= 0) return [0, 0];
```

Replace with:
```javascript
    // Auto-detect base-size resolver (2026-04-19). Falls back to meshBaseSize
    // when meshDetectBaseSize is null/0/undefined — users who never touch the
    // new "检测基准" input see identical behaviour to pre-04-19.
    function _getDetectBaseSize() {
        var detect = Number(state.cfdRoom && state.cfdRoom.meshDetectBaseSize);
        if (detect && detect > 0) return detect;
        var base = Number(state.cfdRoom && state.cfdRoom.meshBaseSize);
        return (base && base > 0) ? base : 0.35;
    }

    function computeMinSafeLevel(baseSize, patchHeight) {
        if (patchHeight <= 0 || baseSize <= 0) return [0, 0];
```

- [ ] **Step 3: Swap 5 auto-detect callsites to use `_getDetectBaseSize()`**

All five sites have the same pattern: `var baseSize = state.cfdRoom.meshBaseSize || 0.4;`. Use `Edit` with `replace_all=true` since the fallback literal `0.4` makes the line unambiguous (but verify — run the grep below first).

Run:
```bash
grep -n "var baseSize = state.cfdRoom.meshBaseSize || 0.4;" STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: 5 matches at lines 25981, 26061, 26203, 26330, 26387.

Then edit via Edit with `replace_all=true`:

Old:
```javascript
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
```

New:
```javascript
        var baseSize = _getDetectBaseSize();
```

- [ ] **Step 4: Verify all 5 sites changed**

Run:
```bash
grep -n "meshBaseSize || 0.4" STLGeneratorForOpenFoam/web/STL_generate.html
grep -n "_getDetectBaseSize()" STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: first grep returns 0 matches; second grep returns 6 matches (1 definition + 5 callsites).

- [ ] **Step 5: Commit**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(mesh-auto-detect): route auto-detect through _getDetectBaseSize()

Five call-sites in STL_generate.html now read from a common resolver
that prefers meshDetectBaseSize (new, nullable) and falls back to
meshBaseSize. Behaviour unchanged when users never touch the new field.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend UI — 检测基准 input row above auto-detect button

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add input row at line 162 area + `ms-detect-base` sync in `_syncMeshPanel`)

- [ ] **Step 1: Add the `.prop-row` directly above the auto-detect button**

Find line 162 (the `🔍 自动检测加密层级` button). The line directly before it is the closing `</div>` of the preceding per-category refinement grid. Insert the new row between that closing `</div>` and the button.

Find this exact block (lines 160-162):
```html
                    <input type="number" id="ms-feat-baffle" min="0" max="8" value="2" class="mesh-lvl-input" oninput="window.setMeshRefineLevel('baffleFeatureLevel',0,+this.value)">
                </div>
                <button class="btn" onclick="window.runAutoDetectRefinement()" style="width:100%; margin-top:10px; padding:6px 10px; font-size:0.82rem;" data-i18n="btn.auto_detect_refinement">🔍 自动检测加密层级</button>
```

Replace with:
```html
                    <input type="number" id="ms-feat-baffle" min="0" max="8" value="2" class="mesh-lvl-input" oninput="window.setMeshRefineLevel('baffleFeatureLevel',0,+this.value)">
                </div>
                <div class="prop-row" style="margin-top:10px;">
                    <label title="自动检测算法的基准值；默认继承基础尺寸。手动改动后,level=ceil(log2(X/(H/2))) 的 X 用这个值,不再跟基础尺寸耦合。">检测基准 (m)</label>
                    <div style="display:flex; gap:4px; align-items:center;">
                        <input type="number" id="ms-detect-base" step="0.01" min="0.01" max="2" style="flex:1;" oninput="window.setMeshConfig('meshDetectBaseSize', parseFloat(this.value) || null)">
                        <button type="button" title="清除 (回到继承基础尺寸)" style="padding:2px 6px; font-size:11px;" onclick="window.setMeshConfig('meshDetectBaseSize', null); document.getElementById('ms-detect-base').value=''; document.getElementById('ms-detect-base').placeholder='继承 ' + ((state.cfdRoom.meshBaseSize||0.35).toFixed(3)) + ' m';">✖</button>
                    </div>
                </div>
                <button class="btn" onclick="window.runAutoDetectRefinement()" style="width:100%; margin-top:10px; padding:6px 10px; font-size:0.82rem;" data-i18n="btn.auto_detect_refinement">🔍 自动检测加密层级</button>
```

- [ ] **Step 2: Wire the UI-sync to populate the input on project load**

Find line 25655 (the `set('ms-base-size', cr.meshBaseSize);` call inside `_syncMeshPanel` / panel-refresh). Add a sibling line after it.

Old:
```javascript
        set('ms-base-size', cr.meshBaseSize);
```

New:
```javascript
        set('ms-base-size', cr.meshBaseSize);
        // 2026-04-19: detect-base is nullable. When null, show empty value + placeholder
        // showing the inherited baseSize so user knows what's in effect.
        var dbEl = document.getElementById('ms-detect-base');
        if (dbEl) {
            if (cr.meshDetectBaseSize != null && cr.meshDetectBaseSize > 0) {
                dbEl.value = cr.meshDetectBaseSize;
                dbEl.placeholder = '';
            } else {
                dbEl.value = '';
                dbEl.placeholder = '继承 ' + (Number(cr.meshBaseSize) || 0.35).toFixed(3) + ' m';
            }
        }
```

- [ ] **Step 3: Make the placeholder track `meshBaseSize` edits too**

When the user changes `meshBaseSize` while detect-base is null, the placeholder should reflect the new inherited value.

Find line ~25721 (`if (key === 'meshBaseSize') _refreshBmGridIfVisible();`). Append a placeholder refresh.

Old:
```javascript
        if (key === 'meshBaseSize') _refreshBmGridIfVisible();
```

New:
```javascript
        if (key === 'meshBaseSize') {
            _refreshBmGridIfVisible();
            // Refresh detect-base placeholder (shows "继承 X m" when detect is null)
            var _db = document.getElementById('ms-detect-base');
            if (_db && !_db.value) {
                _db.placeholder = '继承 ' + (Number(value) || 0.35).toFixed(3) + ' m';
            }
        }
```

- [ ] **Step 4: Verify the HTML loads and JS parses**

Start the STLGen server and reload in browser-use:

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null; sleep 2
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir case/Tri_angle > /tmp/stlgen.log 2>&1 &
sleep 4
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# Login
browser-use eval 'fetch("/api/auth/login", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({username:"admin", password:"WIN#@desk"}), credentials:"include"}).then(r=>r.json()).then(j=>{document.title = "login:" + j.ok;})'
sleep 2
browser-use eval 'location.reload(true)'
sleep 3
# Assert helper + new state field + new input exist
browser-use eval '"has=" + (typeof _getDetectBaseSize === "function") + ",msDB=" + !!document.getElementById("ms-detect-base") + ",stateKey=" + ("meshDetectBaseSize" in state.cfdRoom)'
```

Expected: `"has=true,msDB=true,stateKey=true"`.

- [ ] **Step 5: Commit**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(mesh-ui): add 检测基准 input row above auto-detect button

Single-row number input + ✖ clear button, sits just above the existing
auto-detect button in the mesh settings panel. Empty value →
placeholder shows "继承 0.350 m" and autodetect inherits meshBaseSize.
Typed value → written to state.cfdRoom.meshDetectBaseSize and used
verbatim by _getDetectBaseSize().

Placeholder tracks meshBaseSize edits so the inherited value stays
accurate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Persist `meshDetectBaseSize` in cfdrun + cfdproj exports

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (cfdrun export at line 29183, cfdproj export wherever it serializes `cfdRoom`)

- [ ] **Step 1: Add `detectBaseSize` to cfdrun export**

Find line 29183 (`baseSize: state.cfdRoom.meshBaseSize,` inside the `meshConfig:` object). Insert a sibling directly after it.

Old:
```javascript
            meshConfig: {
                baseSize: state.cfdRoom.meshBaseSize,
                maxGlobalCells: parseInt(state.cfdRoom.maxGlobalCells) || 5000000,
```

New:
```javascript
            meshConfig: {
                baseSize: state.cfdRoom.meshBaseSize,
                // 2026-04-19: detectBaseSize is omitted when null/0 (matches
                // pydantic exclude_none on runner side; keeps existing files
                // diff-clean on re-save).
                ...(state.cfdRoom.meshDetectBaseSize && state.cfdRoom.meshDetectBaseSize > 0
                    ? { detectBaseSize: state.cfdRoom.meshDetectBaseSize }
                    : {}),
                maxGlobalCells: parseInt(state.cfdRoom.maxGlobalCells) || 5000000,
```

- [ ] **Step 2: cfdproj save needs NO extra change**

Verified: `serializeProjectState()` at `STL_generate.html:15235` does `JSON.stringify(state, null, 2)` — the entire `state` object including `state.cfdRoom.meshDetectBaseSize` round-trips automatically. The loader at `_applyProjectData` (line 15285) calls `normalizeState(data)` which restores the whole `cfdRoom`. **No code change required for cfdproj persistence.**

Sanity-check this assumption:
```bash
grep -n "function serializeProjectState\|function normalizeState" STLGeneratorForOpenFoam/web/STL_generate.html
```

Expected: both found. If `normalizeState` is cherry-picking fields (i.e., whitelist-style), add `meshDetectBaseSize` to its list. Based on the current grep at plan-write time it does a wholesale copy.

- [ ] **Step 3: Browser-use persistence test — trigger the real cfdrun export**

The cfdrun payload is built inline inside `exportCFDProject` (the `const cfdData = {...}` block at ~line 29164), not in a named function. We verify by running the real export flow and inspecting the emitted file.

Server must still be running on `case/Tri_angle`. Set detect and export:

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use eval '
state.cfdRoom.meshDetectBaseSize = 0.178;
document.title = "set:" + state.cfdRoom.meshDetectBaseSize;
'
sleep 1

# Trigger the real export (writes case/Tri_angle/model/Tri_angle.cfdrun
# via /api/save-cfdrun or similar — same path the "导出 CFD 工程包" button uses).
browser-use eval 'window.exportCFDProject()'
sleep 5

# Read the emitted cfdrun and check meshConfig keys:
python3 -c "
import json
d = json.load(open('/mnt/lv/litianyu/mycode/dcdtuni/case/Tri_angle/model/Tri_angle.cfdrun'))
mc = d.get('meshConfig', {})
print('baseSize:', mc.get('baseSize'), 'detectBaseSize:', mc.get('detectBaseSize'))
"
```

Expected: `baseSize: 0.35 detectBaseSize: 0.178`.

- [ ] **Step 4: Round-trip with null — detectBaseSize must be OMITTED (not serialised as null)**

```bash
browser-use eval '
state.cfdRoom.meshDetectBaseSize = null;
window.exportCFDProject();
'
sleep 5

python3 -c "
import json
d = json.load(open('/mnt/lv/litianyu/mycode/dcdtuni/case/Tri_angle/model/Tri_angle.cfdrun'))
mc = d.get('meshConfig', {})
print('hasKey:', 'detectBaseSize' in mc, 'baseSize:', mc.get('baseSize'))
"
```

Expected: `hasKey: False baseSize: 0.35` — the conditional spread in Step 1 omits the key when null, matching the pydantic `exclude_none` behaviour on the runner side.

- [ ] **Step 5: Commit**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "$(cat <<'EOF'
feat(mesh-ui): persist meshDetectBaseSize in cfdrun/cfdproj export

Null → key omitted (matches pydantic exclude_none on runner side; keeps
existing project files diff-clean). Non-null → serialised as a float
under meshConfig.detectBaseSize.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: End-to-end verification + submodule bump + final commit

**Files:** no new code. Verifies the full stack works together.

- [ ] **Step 1: End-to-end auto-detect verification via browser-use**

Per spec §Testing Strategy / Change 3. The test is: same scene → same cfdrun → different detectBase → predictably different levels.

Server must still be running on `case/Tri_angle`. If not:
```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null; sleep 2
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir case/Tri_angle > /tmp/stlgen.log 2>&1 &
sleep 4
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval 'fetch("/api/auth/login", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({username:"admin", password:"WIN#@desk"}), credentials:"include"}).then(r=>r.json()).then(j=>{document.title="login:"+j.ok})'
sleep 2
browser-use eval 'location.reload(true)'
sleep 3
```

Load the Tri_angle project. Then:

```bash
# Load Tri_angle cfdproj
browser-use eval '
fetch("/api/read-file?path=STLGeneratorForOpenFoam/projects/Tri_angle.cfdproj").then(r=>r.text()).then(t=>{
  var j = JSON.parse(t);
  if (window._applyProjectData) window._applyProjectData(j);
  window._rebuildModelMap();
  rebuildGeometry();
  document.title = "loaded:" + state.models.length;
});
'
sleep 3
# Baseline auto-detect with detectBase = null (inherited 0.35)
browser-use eval '
state.cfdRoom.meshDetectBaseSize = null;
var result = autoDetectRackRefinement();
var doorKeys = Object.keys(state.cfdRoom.rackDoorPerMinUnitLevel || {});
document.title = "base=null,detect=" + _getDetectBaseSize() + ",door=" + JSON.stringify(state.cfdRoom.rackDoorPerMinUnitLevel);
'
sleep 1
browser-use eval 'document.title'
# Record the door per-unit levels — the 2U bucket should be [3,4].
```

Expected: `_getDetectBaseSize()` returns 0.35; 2U door bucket is `[3,4]` (matches the baseline we computed in the spec).

```bash
# Now set detectBase = 0.178 (4U) — auto-detect should drop levels by 1
browser-use eval '
state.cfdRoom.meshDetectBaseSize = 0.178;
// Reset existing auto-detect result so we re-run cleanly
state.cfdRoom.rackDoorPerMinUnitLevel = {};
state.cfdRoom.serverFacePerUnitLevel = {};
var result = autoDetectRackRefinement();
document.title = "detect=0.178,door=" + JSON.stringify(state.cfdRoom.rackDoorPerMinUnitLevel);
'
sleep 1
browser-use eval 'document.title'
```

Expected: 2U door bucket becomes `[2,3]` (one level lower than baseline) because `ceil(log2(0.178/0.04445)) = 2` instead of `3`. If you see `[3,4]` still, `_getDetectBaseSize` isn't being called on the auto-detect hot path — return to Task 4 and check all 5 callsites.

- [ ] **Step 2: Clear detect-base — back to baseline**

```bash
browser-use eval '
state.cfdRoom.meshDetectBaseSize = null;
state.cfdRoom.rackDoorPerMinUnitLevel = {};
state.cfdRoom.serverFacePerUnitLevel = {};
autoDetectRackRefinement();
document.title = "cleared,door=" + JSON.stringify(state.cfdRoom.rackDoorPerMinUnitLevel);
'
sleep 1
browser-use eval 'document.title'
```

Expected: 2U bucket back to `[3,4]` (same as Step 1 baseline).

- [ ] **Step 3: Mesh regression on Tri_angle**

Spec §Testing Strategy / Change 2: load the existing Tri_angle cfdrun (already at baseSize=0.35), kick a mesh, check cell count is in the same order of magnitude as before the quality-params change.

```bash
# Server is on case/Tri_angle. Kick mesh via the UI's mesh-only endpoint.
curl -s -c /tmp/cookie.txt -X POST http://localhost:8005/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"WIN#@desk"}' | head -1
curl -s -b /tmp/cookie.txt -X POST http://localhost:8005/api/run-mesh -H "Content-Type: application/json" -d '{"caseDir":"case/Tri_angle","meshOnly":true}' | head -5
# Wait for mesh — Tri_angle is small, ~1 min. Poll the log.
# After log shows "Mesh generation complete", fetch mesh stats:
curl -s -b /tmp/cookie.txt -X POST http://localhost:8005/api/mesh-preview/load | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'nCells: {d.get(\"nCells\")}, nBoundaryFaces: {d.get(\"nBoundaryFaces\")}, patches: {len(d.get(\"patches\",[]))}')
"
```

Expected: `nCells` in the range 130k-250k (previous Tri_angle run was 173k at baseSize=0.35 before the quality changes; the new params can change this ±30%). `nBoundaryFaces` ~30k-80k. Patches ~70.

If cell count balloons >500k or drops <50k, something is wrong — probably `nCellsBetweenLevels 4` is spreading too aggressively. Check the snappyHexMeshDict that was generated:
```bash
grep -E "nCellsBetweenLevels|nFeatureSnapIter|maxNonOrtho" case/Tri_angle/Mesh/system/snappyHexMeshDict
```
It should show `4`, `15`, `60`, `70`.

- [ ] **Step 4: Full pytest run — no regressions**

```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ --ignore=src/backend/aircooling/runner/tests/test_partition_hole_fvoptions.py -q
```

Expected: `144 passed` (all previous + 2 new test files from Tasks 2 and 3).

- [ ] **Step 5: Submodule bump commit (ties outer to submodule HEAD)**

After committing all 6 STLGen submodule commits (Tasks 4, 5, 6 each added one), the outer repo shows `M STLGeneratorForOpenFoam` in status. Commit that pointer update plus any outer-repo work from Tasks 2/3.

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git status --short
# Expect: M STLGeneratorForOpenFoam
# (Tasks 2 and 3 already committed their outer-repo changes.)
git add STLGeneratorForOpenFoam
git commit -m "$(cat <<'EOF'
chore(submodule): bump STLGen — meshDetectBaseSize UI + snappy param commits

Pulls in three commits on the STLGen side:
  - _getDetectBaseSize() helper + 5 auto-detect site swaps
  - 检测基准 input row + ms-detect-base sync
  - cfdrun/cfdproj persistence for meshDetectBaseSize

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Final sanity — working tree clean in both repos**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni status
git -C /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam status
```

Expected both: `nothing to commit, working tree clean`.
