# Y-up Coordinate System Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the entire pipeline (Three.js frontend → cfdproj/cfdrun → Python runner → OpenFOAM templates → Omniverse USD) from Z-up to native Y-up right-handed coordinates, with default gravity `(0, -9.81, 0)`.

**Architecture:** Hard cutover, no compatibility shim. Every place that assumes "Z is up" is rewritten to assume "Y is up". Existing `.cfdproj` project files and solved `case/` directories are invalidated by this change — user rebuilds / re-solves them manually.

**Tech Stack:** Three.js r128 (browser prototype), Python 3.10 + Pydantic + Jinja2 (runner), OpenFOAM v2406 (solver), Omniverse Kit SDK + OpenUSD (desktop app).

---

## Decomposition

Tasks are ordered so backend tests can be written first (mechanical TDD), frontend model changes come after the THREE.js axis flip (otherwise nothing renders correctly), and integration verification sits last.

1. **T1 — Backend tests** (Python+template failing tests for gravity default, height validation, constant/g output)
2. **T2 — Backend implementation** (`openfoam_case.py`, `stlgen_runner.py`, `cfdproj_schema.py`, `constant/g`)
3. **T3 — Frontend THREE.js core** (`DefaultUp`, `WORLD_UP`, `camera.up`, background grid, axes sprites, gizmo labels)
4. **T4 — Frontend gravity UI + state defaults**
5. **T5 — Frontend i18n strings** (Z-axis references in zh-CN.json + en-US.json)
6. **T6 — Frontend Rack model** (base.H along Y, slot stacking, fan-server U-slot split)
7. **T7 — Frontend Baffle / Floor / Wall models** (default normals, "bottom" direction)
8. **T8 — Frontend Box / Custom STL / 2D floor plan** (default orientation, plan XY → world XZ mapping)
9. **T9 — Frontend camera view presets** (top/front/side buttons, auto-orbit)
10. **T10 — Omniverse USD upAxis declaration** (`UsdGeom.SetStageUpAxis(stage, Tokens.y)`)
11. **T11 — Integration verification** (browser-use + server `/api/run-mesh` dry run on a fresh Y-up case)

---

## File Map

**Python runner** (`src/backend/aircooling/runner/`):
- `openfoam_case.py:240-267` — `_write_gravity_file` default `gz=-9.81` → `gy=-9.81`
- `stlgen_runner.py:801-821` — `validate_geometry` uses `floor.anchor.z` → `floor.anchor.y`
- `cfdproj_schema.py` — `room.height` + gravity field docstrings
- `tests/test_openfoam_case_gravity.py` (new) — gravity default rendering
- `tests/test_stlgen_validation.py` (update) — assert validation uses `.y` key

**OpenFOAM templates** (`src/backend/aircooling/third_parties/foam/template/`):
- `constant/g` line 19 — `value (0 0 -9.81)` → `value (0 -9.81 0)`
- `mesh/blockMeshDict.j2` — comment header (`y` is vertical)
- `mesh/snappyHexMeshDict.j2` — comment header; no functional change (locationInMesh is passed in, not hardcoded)

**Frontend monolith** (`STLGeneratorForOpenFoam/web/STL_generate.html`, ~7300 lines):
- Line 1134-1135 — `DefaultUp` + `WORLD_UP`
- Line 1152 — `camera.up.copy(WORLD_UP)` (already uses WORLD_UP; auto-flips)
- Line 1277-1321 — `AxesHelper` + axis sprites + gizmo face panels
- Line 1489-1499 — `backgroundGrid` rotation + position
- Line 29567-29576 — grid scroll tracking (position.x/position.y → position.x/position.z)
- Lines 239-241 — gravity input `<div class="prop-row">` for X/Y/Z default values
- Line 2886-2888 — `cfdRoom` default gravity state
- Line 26550-26552 — `_syncUIFromSolverConfig` gravity read
- Line 29024-29027 — project export gravity write
- Line 703 — button i18n key `toolbar.show_grid` (label text)
- Lines 3097, 3115, 3124, 5164, 5165, 5206, 5214, 5377 — rack + equipment Z-axis placement
- Line 2291, 2294 — rack base.H computation
- Line 5815, 5869, 7267 — face normal presets, camera up overrides

**Frontend i18n** (`STLGeneratorForOpenFoam/web/i18n/`):
- `zh-CN.json:156` — `hint.gravity_desc`
- `zh-CN.json:225-226` — `toolbar.show_grid`, `toolbar.hide_grid`
- `en-US.json:151` — `hint.gravity_desc`
- `en-US.json:220-221` — `toolbar.show_grid`, `toolbar.hide_grid`

**Omniverse** (`STLGeneratorForOpenFoam/omniverse/`):
- `source/extensions/stlgen.core/stlgen/core/extension.py` — `on_startup` adds `UsdGeom.SetStageUpAxis`

---

## Task 1 — Backend Failing Tests

**Files:**
- Create: `src/backend/aircooling/runner/tests/test_openfoam_case_gravity.py`
- Modify: `src/backend/aircooling/runner/tests/test_stlgen_validation.py`

- [ ] **Step 1: Write gravity default test**

Create `src/backend/aircooling/runner/tests/test_openfoam_case_gravity.py`:

```python
"""Gravity field default direction after Y-up refactor (2026-04-18)."""
from pathlib import Path

from dcdtuni.runner.openfoam_case import _write_gravity_file


def test_default_gravity_is_y_down(tmp_path: Path):
    """When no gravity dict is supplied, _write_gravity_file must default
    to Y-down: value (0 -9.81 0). Post-refactor, Y is the vertical axis."""
    _write_gravity_file(tmp_path, gravity={})
    g_text = (tmp_path / "g").read_text()
    assert "value           (0 -9.81 0);" in g_text, (
        f"Expected gravity along -Y; got:\n{g_text}"
    )


def test_gravity_dict_overrides_default(tmp_path: Path):
    """Explicit gravity dict wins over default."""
    _write_gravity_file(tmp_path, gravity={"x": 0, "y": 0, "z": -9.81})
    g_text = (tmp_path / "g").read_text()
    assert "value           (0 0 -9.81);" in g_text
```

- [ ] **Step 2: Write height validation test**

Append to `src/backend/aircooling/runner/tests/test_stlgen_validation.py`. Read the existing file first to understand its fixtures. Add:

```python
def test_validate_geometry_checks_floor_anchor_y(tmp_path):
    """Post-refactor, validate_geometry must raise when floor.anchor.y
    exceeds room.height (not floor.anchor.z)."""
    from dcdtuni.runner.stlgen_runner import STLGenRunner

    cfdrun = tmp_path / "case.cfdrun"
    cfdrun.write_text(json.dumps({
        "room": {"bounds": {"xMin": 0, "xMax": 5, "yMin": 0, "yMax": 3,
                             "zMin": 0, "zMax": 5}, "height": 3.0,
                  "temperature": 20.0},
        "floors": {"f1": {"geometry": {"anchor": {"x": 0, "y": 5.0, "z": 0},
                                        "size": {"W": 5, "D": 5, "H": 0.1}},
                           "kind": "floor", "enabled": True}},
        "models": {}, "bafflePlates": {},
        "simulationConfig": {"solverTemplate": "steady",
                             "numerical": {"maxIterations": 100},
                             "physics": {}},
    }))
    runner = STLGenRunner(cfdrun_path=cfdrun, case_dir=tmp_path)
    with pytest.raises(RuntimeError, match=r"y=5\.0 exceeds room.height=3\.0"):
        runner.validate_geometry()
```

(If the existing test file already imports json/pytest, don't duplicate the imports.)

- [ ] **Step 3: Run both tests — expect failure**

Run:
```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_openfoam_case_gravity.py src/backend/aircooling/runner/tests/test_stlgen_validation.py::test_validate_geometry_checks_floor_anchor_y -v
```

Expected: `test_default_gravity_is_y_down` FAILS ("value           (0 0 -9.81);" found, not `(0 -9.81 0)`). `test_validate_geometry_checks_floor_anchor_y` FAILS (current code checks `.z`, not `.y`).

- [ ] **Step 4: Commit failing tests**

```bash
git add src/backend/aircooling/runner/tests/test_openfoam_case_gravity.py \
        src/backend/aircooling/runner/tests/test_stlgen_validation.py
git commit -m "test(runner): Y-up gravity default + height validation (failing)"
```

---

## Task 2 — Backend Implementation

**Files:**
- Modify: `src/backend/aircooling/runner/openfoam_case.py:244`
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:810-821`
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py` (docstring only)
- Modify: `src/backend/aircooling/third_parties/foam/template/constant/g:19`
- Modify: `src/backend/aircooling/third_parties/foam/template/mesh/blockMeshDict.j2` (header comment)

- [ ] **Step 1: Flip `_write_gravity_file` default**

In `src/backend/aircooling/runner/openfoam_case.py:242-244`, change:

```python
    gx = gravity.get("x", 0)
    gy = gravity.get("y", 0)
    gz = gravity.get("z", -9.81)
```

to:

```python
    gx = gravity.get("x", 0)
    gy = gravity.get("y", -9.81)
    gz = gravity.get("z", 0)
```

- [ ] **Step 2: Flip `validate_geometry` axis**

In `src/backend/aircooling/runner/stlgen_runner.py:801-821`, replace the body of `validate_geometry` so the axis check reads `.y` instead of `.z`. The surrounding docstring and error message also update:

```python
    def validate_geometry(self) -> None:
        """Pre-flight check: ensure room.height is consistent with floor y-coordinates.

        Raises RuntimeError if any floor's anchor y exceeds room.height, which would
        cause the room enclosure walls to be shorter than the geometry, leading to
        unclosed mesh domains and baffle seed placement failures.
        """
        if self._data is None:
            self._load_cfd_json_only()
        room_height = self._data.get("room", {}).get("height", 0)
        _TOLERANCE = 1e-3  # allow sub-mm floating-point rounding from JS export
        for name, floor in self._data.get("floors", {}).items():
            y = floor.get("geometry", {}).get("anchor", {}).get("y", 0)
            if y > room_height + _TOLERANCE:
                raise RuntimeError(
                    f"floor '{name}' y={y} exceeds room.height={room_height}. "
                    f"The room enclosure (walls) only extends to y={room_height}, "
                    f"but this floor is placed above it. "
                    f"Update the room height in the 2D floor plan (房间 2D 平面设计 → 房间高度) "
                    f"to at least {y}."
                )
```

- [ ] **Step 3: Update `constant/g` static template**

In `src/backend/aircooling/third_parties/foam/template/constant/g`, change line 19:

```
value           (0 0 -9.81);
```

to:

```
value           (0 -9.81 0);
```

- [ ] **Step 4: Update cfdproj_schema docstring**

In `src/backend/aircooling/runner/cfdproj_schema.py`, find the `height` field on `CFDExportRoom` (around line 54) and update its description. Example — read the actual current Field(...) declaration, then change the description string to mention "vertical extent along Y axis (post-2026-04-18 Y-up refactor)". Also update gravity fields if they have descriptions referencing Z-down.

If the current declaration is `height: float = Field(default=3.0, description="Room height in meters")`, change to:
```python
height: float = Field(
    default=3.0,
    description="Room height in meters (vertical extent along Y; Y-up convention)",
)
```

- [ ] **Step 5: Update blockMeshDict.j2 header comment**

Add one comment line near the top of `src/backend/aircooling/third_parties/foam/template/mesh/blockMeshDict.j2` noting the axis convention. Example — add after the OpenFOAM auto-generated header:

```jinja
// 2026-04-18: Y-up coordinate convention. Vertical extent = Y;
// blockMesh vertices follow (x y z) ordering where y is vertical.
```

(No functional change — blockMesh ordering is fixed by OpenFOAM itself.)

- [ ] **Step 6: Run backend tests — all must pass**

Run:
```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v
```

Expected: all tests pass, including the two new ones from T1.

If any other test now fails because it was asserting Z-up behaviour, fix the test to assert Y-up — do NOT weaken the assertion. (The refactor is a hard cutover; lingering Z-up assertions are bugs.)

- [ ] **Step 7: Commit backend implementation**

```bash
git add src/backend/aircooling/runner/openfoam_case.py \
        src/backend/aircooling/runner/stlgen_runner.py \
        src/backend/aircooling/runner/cfdproj_schema.py \
        src/backend/aircooling/third_parties/foam/template/constant/g \
        src/backend/aircooling/third_parties/foam/template/mesh/blockMeshDict.j2
git commit -m "feat(backend): Y-up coord refactor — gravity default, height validation, constant/g"
```

---

## Task 3 — Frontend THREE.js Core

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:1134-1135` (DefaultUp / WORLD_UP)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:1277-1324` (AxesHelper + axis sprites + gizmo face labels)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:1489-1499` (backgroundGrid rotation + position)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:29567-29576` (grid scroll tracking)

- [ ] **Step 1: Flip DefaultUp + WORLD_UP**

At `STL_generate.html:1134-1135`, change:

```javascript
    THREE.Object3D.DefaultUp.set(0, 0, 1);
    const WORLD_UP = new THREE.Vector3(0, 0, 1);
```

to:

```javascript
    THREE.Object3D.DefaultUp.set(0, 1, 0);
    const WORLD_UP = new THREE.Vector3(0, 1, 0);
```

No other change needed to `camera.up.copy(WORLD_UP)` — that line flows automatically.

- [ ] **Step 2: Flip background grid from XY plane to XZ plane**

At `STL_generate.html:1489-1499`, the current code creates a `GridHelper` and rotates it `Math.PI / 2` around X to put it in the XY plane (because `GridHelper` defaults to XZ plane). With Y-up, we want the grid in the XZ plane (the floor), which is `GridHelper`'s native orientation — **remove the rotation** and change the ground-attachment axis from Z to Y:

```javascript
    backgroundGrid = new THREE.GridHelper(actualGridSize, divisions, 0x6f87a3, 0x3d4958);
    // 2026-04-18: Y-up. GridHelper is natively XZ plane; no rotation needed.
    backgroundGrid.position.y = 0;
    backgroundGrid.renderOrder = -10;
```

(Delete the `backgroundGrid.rotation.x = Math.PI / 2;` line and change `backgroundGrid.position.z = 0;` to `backgroundGrid.position.y = 0;`.)

- [ ] **Step 3: Fix grid scroll tracking**

At `STL_generate.html:29567-29576`, the scroll-follow code updates `backgroundGrid.position.x` and `.y` to track the orbit target. With Y-up, the grid is horizontal in the XZ plane, so scroll-follow must track X and Z (not X and Y). Change:

```javascript
    backgroundGrid.position.x = Math.round(target.x / scrollStep) * scrollStep;
    backgroundGrid.position.y = Math.round(target.y / scrollStep) * scrollStep;
```

to:

```javascript
    backgroundGrid.position.x = Math.round(target.x / scrollStep) * scrollStep;
    backgroundGrid.position.z = Math.round(target.z / scrollStep) * scrollStep;
```

- [ ] **Step 4: Audit gizmo face panels + axis sprites**

At `STL_generate.html:1277-1324`, the axis sprite placement and the `VIEW_PRESETS` gizmo panel encode Z as up. Read that block (`axesGroup`, `axisSprites`, `gizmoFaces` / `VIEW_PRESETS`, whichever names apply), then audit: any `new THREE.Vector3(0, 0, 1)` meant as "up" must become `new THREE.Vector3(0, 1, 0)`, and sprite labels like `'Z'` at position `(0, 0, 1.7)` are still valid (that sprite IS labelling the Z axis, it just happens to be horizontal now — that's correct; the label follows the axis, not "up").

For the gizmo face buttons (`+Z`, `-Z`, etc. at line 1321 area), the camera positions they drive must still point the camera at that face — no code change needed to the face data, only ensure the camera `up` vector each preset uses respects Y-up. Read line 1321:
```javascript
{ label: '+Z', axis: new THREE.Vector3(0, 0, 1), color: '#2f64aa', position: [0, 0, 0.51], rotation: [0, 0, 0] },
```
The `axis` and `position` are the axis the label refers to — leave them. If the preset ALSO sets `up`, ensure the up-vector is the Y-up world up `(0, 1, 0)` instead of a hardcoded Z preset. (Check the render code that consumes these presets — likely line 7267 `up: new THREE.Vector3(0, 0, 1)`.)

Change `STL_generate.html:7267`:

```javascript
                    up:     new THREE.Vector3(0, 0, 1),
```

to:

```javascript
                    up:     new THREE.Vector3(0, 1, 0),
```

- [ ] **Step 5: Audit "floor normal" and "bottom" unit vectors**

At `STL_generate.html:5815` and `:5869`, search for the `bottom` entry in face-direction maps and the `up` vector used in extrusion logic. Any hardcoded `new THREE.Vector3(0, 0, 1)` meaning "up from the floor" becomes `new THREE.Vector3(0, 1, 0)`; any meaning "the Z axis" (e.g., a face label) stays.

Specifically at line 5815:
```javascript
            bottom: new THREE.Vector3(0, 0, 1)
```
If `bottom` here means "normal pointing up from a bottom face" (which is the convention in this file), change to `new THREE.Vector3(0, 1, 0)`. If it means "the -Y direction of a face local basis", leave. Read the surrounding block to decide.

At line 5869:
```javascript
        const up = new THREE.Vector3(0, 0, 1).applyQuaternion(quaternion);
```
`up` as the variable name is unambiguous — change to `new THREE.Vector3(0, 1, 0)`.

- [ ] **Step 6: Smoke test — server must load**

Start the dev server:
```bash
fuser -k 8005/tcp 2>/dev/null
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir /tmp/ytest &
sleep 3
curl -s -c /tmp/cookie.txt -X POST http://localhost:8005/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"WIN#@desk"}' | head -100
```

Expected: login returns `{"success":true,...}`.

- [ ] **Step 7: Smoke test — axis invariants via browser-use**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "JSON.stringify({up: THREE.Object3D.DefaultUp.toArray(), cameraUp: camera.up.toArray()})"
```

Expected output: `{"up":[0,1,0],"cameraUp":[0,1,0]}`.

Then screenshot to visually confirm the grid is on the XZ plane (floor) and axes display correctly:
```bash
browser-use screenshot /tmp/y_up_empty.png
browser-use close
```

Read `/tmp/y_up_empty.png` — verify the green grid is horizontal and the red/green/blue axis indicators extend correctly (red=X right, green=Y up, blue=Z toward viewer).

- [ ] **Step 8: Commit core**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(three): Y-up DefaultUp + XZ-plane grid + axis-sprite audit"
```

(Submodule bump commit in main repo comes at the end.)

---

## Task 4 — Frontend Gravity UI + State Defaults

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:239-241` (gravity input defaults)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:2886-2888` (cfdRoom default gravity)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:26550-26552` (UI sync from solver config)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:29024-29027` (project export gravity)

- [ ] **Step 1: Flip gravity input default values**

At `STL_generate.html:239-241`, current HTML:

```html
<div class="prop-row"><label>X</label><input type="number" id="slv-gx" step="0.01" value="0" oninput="window.setSolverConfig('gravityX', parseFloat(this.value))"></div>
<div class="prop-row"><label>Y</label><input type="number" id="slv-gy" step="0.01" value="0" oninput="window.setSolverConfig('gravityY', parseFloat(this.value))"></div>
<div class="prop-row"><label>Z</label><input type="number" id="slv-gz" step="0.01" value="-9.81" oninput="window.setSolverConfig('gravityZ', parseFloat(this.value))"></div>
```

Change the `value=` attributes so Y is `-9.81` and Z is `0`:

```html
<div class="prop-row"><label>X</label><input type="number" id="slv-gx" step="0.01" value="0" oninput="window.setSolverConfig('gravityX', parseFloat(this.value))"></div>
<div class="prop-row"><label>Y</label><input type="number" id="slv-gy" step="0.01" value="-9.81" oninput="window.setSolverConfig('gravityY', parseFloat(this.value))"></div>
<div class="prop-row"><label>Z</label><input type="number" id="slv-gz" step="0.01" value="0" oninput="window.setSolverConfig('gravityZ', parseFloat(this.value))"></div>
```

- [ ] **Step 2: Flip cfdRoom default gravity state**

At `STL_generate.html:2886-2888`:

```javascript
                gravityX: 0,
                gravityY: 0,
                gravityZ: -9.81,
```

change to:

```javascript
                gravityX: 0,
                gravityY: -9.81,
                gravityZ: 0,
```

- [ ] **Step 3: Fix UI sync defaults**

At `STL_generate.html:26550-26552`:

```javascript
        set('slv-gx', cr.gravityX != null ? cr.gravityX : 0);
        set('slv-gy', cr.gravityY != null ? cr.gravityY : 0);
        set('slv-gz', cr.gravityZ != null ? cr.gravityZ : -9.81);
```

change to:

```javascript
        set('slv-gx', cr.gravityX != null ? cr.gravityX : 0);
        set('slv-gy', cr.gravityY != null ? cr.gravityY : -9.81);
        set('slv-gz', cr.gravityZ != null ? cr.gravityZ : 0);
```

- [ ] **Step 4: Fix project export defaults**

At `STL_generate.html:29024-29027`:

```javascript
                    x: state.cfdRoom.gravityX != null ? state.cfdRoom.gravityX : 0,
                    y: state.cfdRoom.gravityY != null ? state.cfdRoom.gravityY : 0,
                    z: state.cfdRoom.gravityZ != null ? state.cfdRoom.gravityZ : -9.81
```

change to:

```javascript
                    x: state.cfdRoom.gravityX != null ? state.cfdRoom.gravityX : 0,
                    y: state.cfdRoom.gravityY != null ? state.cfdRoom.gravityY : -9.81,
                    z: state.cfdRoom.gravityZ != null ? state.cfdRoom.gravityZ : 0
```

- [ ] **Step 5: Verify via browser-use**

With the server still running from T3, reload and verify gravity defaults:

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "JSON.stringify({gx: document.getElementById('slv-gx').value, gy: document.getElementById('slv-gy').value, gz: document.getElementById('slv-gz').value, stateGy: state.cfdRoom.gravityY, stateGz: state.cfdRoom.gravityZ})"
```

Expected: `{"gx":"0","gy":"-9.81","gz":"0","stateGy":-9.81,"stateGz":0}`.

- [ ] **Step 6: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(ui): gravity defaults flip Z→Y for Y-up convention"
```

---

## Task 5 — Frontend i18n Strings

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json:156, 225, 226`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json:151, 220, 221`

- [ ] **Step 1: Update Chinese i18n**

In `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`:

Line 156, change:
```json
"hint.gravity_desc": "设置重力加速度方向和大小（m/s²），默认 Z 轴负方向。",
```
to:
```json
"hint.gravity_desc": "设置重力加速度方向和大小（m/s²），默认 Y 轴负方向（Y-up 坐标系）。",
```

Line 225-226, change:
```json
"toolbar.show_grid": "显示 XOY 网格",
"toolbar.hide_grid": "隐藏 XOY 网格",
```
to:
```json
"toolbar.show_grid": "显示 XOZ 网格",
"toolbar.hide_grid": "隐藏 XOZ 网格",
```

- [ ] **Step 2: Update English i18n**

In `STLGeneratorForOpenFoam/web/i18n/en-US.json`:

Line 151, change:
```json
"hint.gravity_desc": "Set gravity direction and magnitude (m/s²). Default: negative Z-axis.",
```
to:
```json
"hint.gravity_desc": "Set gravity direction and magnitude (m/s²). Default: negative Y-axis (Y-up convention).",
```

Line 220-221, change:
```json
"toolbar.show_grid": "Show XOY Grid",
"toolbar.hide_grid": "Hide XOY Grid",
```
to:
```json
"toolbar.show_grid": "Show XZ Floor Grid",
"toolbar.hide_grid": "Hide XZ Floor Grid",
```

- [ ] **Step 3: Search for any other Z-axis language strings**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/i18n
```

Use Grep on the two JSON files for patterns like `Z 轴`, `Z-axis`, `Z-up`, `垂直 Z`. For each match not already covered above, decide whether it refers to:
- The actual Z axis (as a labelled coordinate direction) — leave it
- The "up" direction by implicit convention — update to Y

Record any other strings edited in the commit message.

- [ ] **Step 4: Also update the raw HTML fallback text**

At `STL_generate.html:238`, the hardcoded HTML fallback (used if i18n fails to load) says:
```html
<div style="font-size:0.77rem; color:var(--text-dim); margin-bottom:6px;" data-i18n="hint.gravity_desc">设置重力加速度方向和大小（m/s²），默认 Z 轴负方向。</div>
```

Change the inner text to match the new zh-CN string:
```html
<div style="font-size:0.77rem; color:var(--text-dim); margin-bottom:6px;" data-i18n="hint.gravity_desc">设置重力加速度方向和大小（m/s²），默认 Y 轴负方向（Y-up 坐标系）。</div>
```

Also update the button label at `STL_generate.html:703`:
```html
<button id="grid-toggle-btn" class="tool-btn" onclick="toggleBackgroundGrid()" data-i18n="toolbar.show_grid">显示 XOY 网格</button>
```
to:
```html
<button id="grid-toggle-btn" class="tool-btn" onclick="toggleBackgroundGrid()" data-i18n="toolbar.show_grid">显示 XOZ 网格</button>
```

- [ ] **Step 5: Reload + eyeball**

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use screenshot /tmp/i18n_check.png
browser-use close
```

Read `/tmp/i18n_check.png` and verify the gravity hint text and grid button both reference Y / XZ.

- [ ] **Step 6: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/i18n/zh-CN.json web/i18n/en-US.json web/STL_generate.html
git commit -m "i18n: update gravity/grid labels for Y-up convention"
```

---

## Task 6 — Frontend Rack Model

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:2291, 2294` (rack.base.H computation)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:3097, 3115-3124` (rack clone + equipment placement)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:5164-5214` (rack anchor bounds)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:5377` (slot layout y vs height)

- [ ] **Step 1: Audit "Z is rack vertical" call sites**

Before editing, enumerate the call sites. Run (via the Grep tool):

Grep pattern: `anchor\.z|slotZ|base\.H`
Path: `/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html`
Expect: ~100 matches. For each, classify:
- **Vertical axis (rack H, slot stacking, floor clearance)** → change to `.y` / `slotY`
- **Pure depth/horizontal (anchor.z as a horizontal coord that was never "up")** → leave as `.z`
- **Neutral coord copy (e.g. `{x: a.x, y: a.y, z: a.z}`)** → leave

The structural axis flip for rack/equipment is: **anchor.z (the vertical position where the rack sits on the floor) becomes anchor.y**. **base.H (which was the rack's extent along +Z) is now its extent along +Y**. **slotZ (the Z-coordinate of each U slot) becomes slotY**.

Produce a list of line numbers to edit. The core expected edits are at the lines above; other matches may be pure-coord and stay.

- [ ] **Step 2: Fix equipment slot Z → Y (at line 3115, 3124)**

At `STL_generate.html:3115`:
```javascript
const slotZ = m.anchor.z + (eq.startUnit || 0) * ((m.rack && m.rack.unitHeight) || 0.04445);
```
becomes:
```javascript
const slotY = m.anchor.y + (eq.startUnit || 0) * ((m.rack && m.rack.unitHeight) || 0.04445);
```

At `STL_generate.html:3124`:
```javascript
hs.anchor = { x: cpModel.anchor.x, y: cpModel.anchor.y, z: cpModel.anchor.z + 0.01 };
```
This is "place the cutout patch slightly above the clone"; +0.01 on Z was "lift off ground". Now "lift off ground" is +Y:
```javascript
hs.anchor = { x: cpModel.anchor.x, y: cpModel.anchor.y + 0.01, z: cpModel.anchor.z };
```

Also update the preceding line that uses `slotZ` to construct the cutout position — it should use `slotY` now.

- [ ] **Step 3: Fix rack.base.H computation context (line 2291, 2294)**

At `STL_generate.html:2291`:
```javascript
model.base.H = rack.unitCount * rack.unitHeight + rack.safeMarginTop + rack.safeMarginBottom;
```

This line is FINE numerically (base.H is a scalar, not an axis). But the meaning of base.H has changed from "Z extent" to "Y extent". No code change here. Just note in a comment:

```javascript
// 2026-04-18: base.H is the rack's vertical extent (along Y, post-Y-up refactor).
model.base.H = rack.unitCount * rack.unitHeight + rack.safeMarginTop + rack.safeMarginBottom;
```

Line 2294 (`slotAreaH`) is similar — scalar; comment optional.

- [ ] **Step 4: Fix rack anchor Z bounds (line 5164-5214)**

At `STL_generate.html:5164-5165`:
```javascript
if (b.zMin.enabled) model.anchor.z = Math.max(b.zMin.value, model.anchor.z);
if (b.zMax.enabled) model.anchor.z = Math.min(b.zMax.value - H, model.anchor.z);
```
These bound-check the VERTICAL axis (the `- H` subtraction for zMax is "rack top must not exceed ceiling"). Change to `.y`:
```javascript
if (b.yMin.enabled) model.anchor.y = Math.max(b.yMin.value, model.anchor.y);
if (b.yMax.enabled) model.anchor.y = Math.min(b.yMax.value - H, model.anchor.y);
```

BUT — this depends on how `b.zMin/zMax/yMin/yMax` are defined in `state.bounds`. Read the bounds schema (grep for `state.bounds`). The bounds object likely has `.xMin/xMax/yMin/yMax/zMin/zMax` — under Y-up, the vertical bound IS yMin/yMax. So the change above assumes the bounds schema was also updated. (See Step 6 below for bounds-schema handling.)

At `STL_generate.html:5186-5214`, the `originAnchor` + `model.anchor.z = originAnchor.z + dir.z * stepZ * ring` pattern is a ring-placement algorithm. It uses anchor.z as the vertical coord. Change pattern:

```javascript
const originAnchor = { x: model.anchor.x, y: model.anchor.y, z: model.anchor.z };
// ...
model.anchor.z = originAnchor.z + dir.z * stepZ * ring;
// ...
model.anchor.z = originAnchor.z;
```

to:

```javascript
const originAnchor = { x: model.anchor.x, y: model.anchor.y, z: model.anchor.z };
// ring placement moved to horizontal XZ plane (post-Y-up). Vertical ring
// stacking is out of scope — rings are in-plane.
model.anchor.z = originAnchor.z + dir.z * stepZ * ring;
// ...
model.anchor.z = originAnchor.z;
```

Actually re-read the block to decide: if the ring places copies around a horizontal circle, under Z-up the "horizontal plane" was XY so ring offsets were in X and Y. Under Y-up, horizontal plane is XZ, so ring offsets should be in X and Z — and the code already does `dir.z * stepZ`, meaning the ring is in XZ, which is correct under Y-up. **No change needed** for the ring placement itself. Add a comment clarifying.

- [ ] **Step 5: Fix slot layout (line 5377)**

At `STL_generate.html:5377`:
```javascript
y: rack.safeMarginBottom + slotIndex * rack.unitHeight,
```

This is a slot's position inside the rack's local coord system. Under Z-up, "y inside rack" was a horizontal coord. Under Y-up, "y inside rack" is the vertical coord (good — slot index should stack vertically along Y).

Read the surrounding block to see how this `y` value is consumed. If it's being passed to a function that expects "position along the vertical axis", the change from "horizontal Y" to "vertical Y" is the correct semantic — but any code downstream that took this `y` value and placed it on Z (e.g. `pos.z = slot.y`) needs flipping.

Search for usages: grep `slotIndex * rack.unitHeight` across the file and audit each consumer.

- [ ] **Step 6: Audit state.bounds axis assignment**

Grep for `state.bounds` and `bounds.zMax` / `bounds.yMax`. Under Y-up, the `height` bound control maps to `yMax` (not `zMax`). If the UI inspector currently writes height-limit values into `bounds.zMax`, flip to `bounds.yMax`. Read the bounds inspector code (likely in the 5100-5400 range, based on proximity to anchor bounds code) and fix.

- [ ] **Step 7: Browser-use smoke test — create default rack**

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.addNewModel('rack'); const m = state.models[state.models.length - 1]; JSON.stringify({H: m.base.H, anchorY: m.anchor.y, anchorZ: m.anchor.z})"
```

Expected: a JSON object with `H` (rack height, typically 1.867 for a 42U rack) and `anchorY` close to 0 (rack sits on floor Y=0). `anchorZ` should be whatever horizontal placement value was chosen.

```bash
browser-use screenshot /tmp/rack_y_up.png
browser-use close
```

Read `/tmp/rack_y_up.png`. Verify: rack stands upright with its long extent going UP (+Y), not sideways. Slots are horizontal shelves stacked vertically.

- [ ] **Step 8: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack): base.H along Y, slot stacking on Y, anchor bounds use yMin/yMax"
```

---

## Task 7 — Frontend Baffle / Floor / Wall Models

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (default baffle normal; floor/wall orientation)

- [ ] **Step 1: Find default baffle normal + "bottom" direction**

Grep for `kind.*floor|kind.*baffle|kind.*wall` near `createDefault` and `addNewModel` calls. For each model type with a default normal, identify:
- Floor baffle default normal: was `(0, 0, 1)` = +Z ("up"). Under Y-up → `(0, 1, 0)`.
- Ceiling baffle default normal: was `(0, 0, -1)` = -Z ("down"). Under Y-up → `(0, -1, 0)`.
- Wall baffle default normal: horizontal, was `(1, 0, 0)` or `(0, 1, 0)`. Under Y-up, a wall still faces horizontally — `(1, 0, 0)` or `(0, 0, 1)`. The specific value depends on which wall (front/back/left/right in world frame).

- [ ] **Step 2: Fix bottom-face unit vector at line 5815**

Re-examine `STL_generate.html:5815`. If `bottom: new THREE.Vector3(0, 0, 1)` was for a face-basis table where bottom-face normals point up (Z-up convention = +Z), under Y-up it becomes `(0, 1, 0)`:
```javascript
bottom: new THREE.Vector3(0, 1, 0)
```

Similarly audit `top`, `front`, `back`, `left`, `right` entries in the same block. Typical mapping:

| Face | Z-up normal | Y-up normal |
|------|-------------|-------------|
| top | (0, 0, -1) | (0, -1, 0) |
| bottom | (0, 0, 1) | (0, 1, 0) |
| front | (0, -1, 0) | (0, 0, -1) |
| back | (0, 1, 0) | (0, 0, 1) |
| left | (-1, 0, 0) | (-1, 0, 0) |
| right | (1, 0, 0) | (1, 0, 0) |

(The top/bottom swap — "bottom faces up" — happens because "bottom" means "the face at the bottom of the box", whose outward normal points DOWN. Under Z-up that's `(0, 0, -1)` not `(0, 0, 1)`. Double-check the sign convention by reading what the "bottom" normal is used for — some code uses inward normals, some outward.)

- [ ] **Step 3: Fix floor-baffle creation direction**

Grep for `createBaffle\|addBaffle\|kind.*baffle` and find the default baffle creation function. Wherever the new baffle's `orientation` or `normal` is hardcoded to a Z-based vector, update to Y-based.

- [ ] **Step 4: Browser-use smoke test — floor baffle**

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.addNewModel('baffle'); const m = state.models[state.models.length - 1]; JSON.stringify({kind: m.kind, normal: m.normal || m.orientation || null, anchorY: m.anchor.y})"
browser-use screenshot /tmp/baffle_y_up.png
browser-use close
```

Read the screenshot. A newly-created baffle should be a horizontal panel (parallel to the floor grid), not a vertical slab.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(baffle): default normals align with Y-up (floor=+Y, ceiling=-Y)"
```

---

## Task 8 — Frontend Box / Custom STL / 2D Floor Plan

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (box default orientation, STL import transform, 2D plan→3D projection)

- [ ] **Step 1: Audit box default creation**

Grep for `addNewModel\('box'\)` and `createDefaultBox`. A parametric box has `base.W/D/H` — under both conventions H is "the vertical extent", so numerically no change. But the placement of the box on the floor was `anchor.z = 0` (box sits on Z=0 floor); under Y-up it's `anchor.y = 0`. Find the default creation code and flip.

- [ ] **Step 2: Audit custom STL import orientation**

Grep for `STLLoader\|importSTL\|applyMatrix4` in the STL import code path. If the import applies any default rotation (e.g., `.rotateX(-Math.PI/2)` to convert from tool-X-forward to Three's Y-up), re-evaluate.

The STL format itself has no axis convention; any rotation applied on import was to correct a tool-specific default (e.g., Blender exports Z-up STL by default). Under Y-up world frame:
- If the STL was authored Y-up (Maya, Unity, ParaView with a Y-up view) — **no** transform needed.
- If Z-up (Blender, ParaView default) — apply `rotateX(-Math.PI/2)` to make "Z up in the file" become "Y up in the world".

The frontend should default to "imported STL is Y-up" (assuming user is exporting from a Y-up tool) and surface a "Z-up (Blender)" checkbox in the import dialog for the Z-up case. If the current code has a hardcoded rotation, move it behind a checkbox. If there's no dialog yet, add the default-Y-up assumption and log a message in the operation log.

(Defer the Z-up import checkbox as a follow-up if it's more than 10 lines — it's not blocking this refactor.)

- [ ] **Step 3: Audit 2D floor plan → 3D conversion**

The 2D floor plan designer uses a 2D coordinate system (screen X/Y). When a 2D polygon is extruded into a 3D model (wall, floor), the mapping was:
- plan X → world X (east)
- plan Y → world Y (north) — horizontal
- extrude → +Z (up)

Under Y-up:
- plan X → world X
- plan Y → world Z (north — horizontal)
- extrude → +Y (up)

Grep for `plan.x|plan.y|extrude|projectTo3D|screen2world` and audit each use. The extrusion direction changes from `+Z` to `+Y`, and the "depth" horizontal axis (was world Y) becomes world Z.

- [ ] **Step 4: Browser-use smoke test — default box + floor plan**

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.addNewModel('box'); const m = state.models[state.models.length - 1]; JSON.stringify({anchor: m.anchor, base: m.base})"
```

Expected: `anchor.y` is 0 (sits on floor), `base.H` is a positive scalar.

```bash
browser-use screenshot /tmp/box_y_up.png
browser-use close
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(models): box/STL/floor-plan creation defaults Y-up"
```

---

## Task 9 — Frontend Camera View Presets

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (VIEW_PRESETS or equivalent)

- [ ] **Step 1: Audit view preset positions + up-vectors**

Find the view-preset block (grep for `VIEW_PRESETS\|+X\|+Y\|+Z` or similar). Each preset has a camera position and a camera up. Typical presets and Y-up values:

| Preset | Look from | Camera up | Z-up old | Y-up new |
|--------|-----------|-----------|----------|----------|
| Top | above, looking down | world forward | up=(0,1,0) pos=(0,0,D) | up=(0,0,-1) pos=(0,D,0) |
| Bottom | below, looking up | world forward | up=(0,1,0) pos=(0,0,-D) | up=(0,0,1) pos=(0,-D,0) |
| Front | in front, looking back | world up | up=(0,0,1) pos=(0,-D,0) | up=(0,1,0) pos=(0,0,D) |
| Back | behind, looking forward | world up | up=(0,0,1) pos=(0,D,0) | up=(0,1,0) pos=(0,0,-D) |
| Left | from -X | world up | up=(0,0,1) pos=(-D,0,0) | up=(0,1,0) pos=(-D,0,0) |
| Right | from +X | world up | up=(0,0,1) pos=(D,0,0) | up=(0,1,0) pos=(D,0,0) |

For Top/Bottom views, the camera up must NOT be parallel to the view direction, so pick a horizontal "forward" direction. Common choice: `(0, 0, -1)` (view with +Z down-screen under Y-up).

Rewrite each preset's `up` and `position` to the Y-up column.

- [ ] **Step 2: Audit `auto orbit to center` and `fit view` logic**

Grep for `fitCameraToBounds\|fitView\|lookAt`. Any `camera.up = new Vector3(0, 0, 1)` hardcoded in these helpers must become `(0, 1, 0)`.

At `STL_generate.html:6066` (based on earlier grep):
```javascript
camera.up.copy(WORLD_UP);
```
This already uses WORLD_UP, so it auto-adapts. No change needed.

- [ ] **Step 3: Browser-use smoke test — view presets**

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.addNewModel('rack')"
browser-use eval "window.applyViewPreset('+Y')"  # or equivalent function name - discover via state inspection
browser-use screenshot /tmp/view_top.png
browser-use eval "window.applyViewPreset('+X')"
browser-use screenshot /tmp/view_right.png
browser-use close
```

If the function name is different, discover it with:
```bash
browser-use eval "Object.keys(window).filter(k => k.toLowerCase().includes('view') || k.toLowerCase().includes('preset')).slice(0, 20).join(', ')"
```

Read the two screenshots. `view_top.png` must show the rack from above (seeing its top face). `view_right.png` must show the rack from its right side.

- [ ] **Step 4: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(camera): view presets rewritten for Y-up (top = look down Y)"
```

---

## Task 10 — Omniverse USD upAxis

**Files:**
- Modify: `STLGeneratorForOpenFoam/omniverse/source/extensions/stlgen.core/stlgen/core/extension.py`
- Create: `STLGeneratorForOpenFoam/omniverse/source/extensions/stlgen.core/stlgen/core/tests/test_stage_up_axis.py` (if test harness exists; otherwise skip to manual verification)

- [ ] **Step 1: Add UsdGeom.SetStageUpAxis on startup**

In `STLGeneratorForOpenFoam/omniverse/source/extensions/stlgen.core/stlgen/core/extension.py`, the current `on_startup` is:

```python
def on_startup(self, ext_id):
    StlgenCoreExtension._instance = self
    self._state = StlgenState()
    print("[stlgen.core] Started")
```

Update to explicitly set the USD stage up-axis. Because USD's default is Y-up, this is belt-and-braces but documents intent and guards against future regressions:

```python
def on_startup(self, ext_id):
    StlgenCoreExtension._instance = self
    self._state = StlgenState()

    try:
        import omni.usd
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        if stage is not None:
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
            print("[stlgen.core] Stage upAxis set to Y (Y-up convention)")
    except Exception as exc:
        print(f"[stlgen.core] Could not set stage upAxis: {exc}")

    print("[stlgen.core] Started")
```

The `try/except` is necessary because `on_startup` runs before a stage may exist; we don't want startup to fail if the USD context is not yet available.

- [ ] **Step 2: Manual verification (no Kit test harness required)**

If the Omniverse dev environment is available, launch and check:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/omniverse
./repo.sh launch 2>&1 | grep "upAxis"
```
Expected: `[stlgen.core] Stage upAxis set to Y (Y-up convention)` appears in the launch log.

If the Omniverse environment is not usable in this session, just verify syntactically by running Python's AST parser:
```bash
conda run -n dcdtuni python -c "import ast; ast.parse(open('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/omniverse/source/extensions/stlgen.core/stlgen/core/extension.py').read())"
```
Expected: no output (clean parse).

- [ ] **Step 3: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add omniverse/source/extensions/stlgen.core/stlgen/core/extension.py
git commit -m "feat(omniverse): explicit UsdGeom.SetStageUpAxis(Y) on startup"
```

---

## Task 11 — Integration Verification

**Files:**
- None edited; this task RUNS the full pipeline on a fresh Y-up project.

- [ ] **Step 1: Kill any stale server, start fresh**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
cd /mnt/lv/litianyu/mycode/dcdtuni
mkdir -p /tmp/y_up_test/model
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir /tmp/y_up_test &
sleep 4
```

- [ ] **Step 2: Log in and set case dir**

```bash
curl -s -c /tmp/cookie.txt -X POST http://localhost:8005/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"WIN#@desk"}'
curl -s -b /tmp/cookie.txt -X POST http://localhost:8005/api/set-case-dir \
    -H "Content-Type: application/json" \
    -d '{"caseDir":"/tmp/y_up_test"}'
```

Expected: both return `{"success":true,...}`.

- [ ] **Step 3: Build a minimal Y-up case via browser-use**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# Assume the UI auto-loaded the case dir on open. If not, invoke the appropriate setter.
browser-use eval "window.createDefaultBaseModel ? window.createDefaultBaseModel() : window.addNewModel('box')"
browser-use eval "window.addNewModel('rack')"
browser-use eval "window.saveProject ? window.saveProject('ytest.cfdproj') : null"
browser-use screenshot /tmp/y_up_scene.png
browser-use close
```

Read `/tmp/y_up_scene.png`. Verify: a box sits on the floor (Y=0) with the rack standing on top/beside it. Both are oriented vertically along Y.

- [ ] **Step 4: Export cfd_export.json and inspect gravity**

```bash
curl -s -b /tmp/cookie.txt -X POST http://localhost:8005/api/export-cfd-project \
    -H "Content-Type: application/json" \
    -d '{"caseName":"ytest"}'
ls /tmp/y_up_test/model/
cat /tmp/y_up_test/model/*.cfdrun | python -c "import json,sys; d=json.load(sys.stdin); print('gravity:', d.get('room',{}).get('gravity', d.get('gravity')))"
```

Expected: gravity prints as `{"x": 0, "y": -9.81, "z": 0}`.

- [ ] **Step 5: Run `--mesh-only` (dry dry — just enough to emit constant/g)**

```bash
cfdrun_file=$(ls /tmp/y_up_test/model/*.cfdrun | head -1)
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --from-cfdrun "$cfdrun_file" --mesh-only 2>&1 | tail -40
```

This will run snappyHexMesh (potentially slow). If it runs to completion, great. If it errors early on something unrelated to gravity/height, that's a different bug — note it and continue.

**Blocking failure mode:** validate_geometry raises because some floor has `anchor.y > room.height`. That means T6 or T8 left a floor at old-coord placement. Fix and re-run.

- [ ] **Step 6: Verify emitted constant/g**

```bash
cat /tmp/y_up_test/Mesh/constant/g | grep '^value'
```

Expected:
```
value           (0 -9.81 0);
```

- [ ] **Step 7: Cleanup and commit the submodule pointer**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null

cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — Y-up coord refactor (T3-T10)"
```

- [ ] **Step 8: Summary / acceptance**

All acceptance criteria must be satisfied:

1. ✅ All Python unit tests pass (`pytest src/backend/aircooling/runner/tests/ -v`).
2. ✅ Browser screenshot shows default rack standing along +Y (verified in T6 Step 7).
3. ✅ `_write_gravity_file` default emits `(0 -9.81 0)` (verified in T2 Step 6 + T11 Step 6).
4. ✅ `floor.anchor.y > room.height` raises `RuntimeError` (verified in T2 Step 6).
5. ✅ Omniverse extension explicitly sets stage upAxis to Y (verified in T10 Step 2).
6. ✅ A fresh Y-up project round-trips through browser → cfdrun → `--mesh-only` → constant/g with the right gravity vector (verified in T11 Steps 3-6).

If all six check, the refactor is complete.

---

## Appendix — Known Not-Covered Scope

These are intentionally OUT of scope for this plan:

- **Migrating existing 40 `.cfdproj` files.** User rebuilds manually.
- **Migrating existing solved `case/*/Mesh/`, `case/*/0/`, `case/*/postProcessing/`.** User re-solves.
- **ParaView state files (`.pvsm`).** None versioned; user re-configures camera once.
- **"Z-up STL import" checkbox.** Deferred; if user imports a Z-up STL (e.g., from Blender default export), it will appear lying on its side. Manual workaround: rotate 90° around X in the STL editor after import.
- **Liquid cooling runner (`liquid_runner.py`).** If it reuses `openfoam_case.py`'s gravity writer, it inherits the fix. If it hardcodes gravity separately, out of scope for this plan (flag and file follow-up).
- **Any case-specific `cfdrun` files with explicit old-style gravity values.** Those are user-owned and out of scope.

---

## Plan Self-Review

**Spec coverage:** Every item in the 5-point scope brief (HTML core, HTML rack/baffle, OpenFOAM templates, Python runner, Omniverse) has a dedicated task. Gravity defaults (mentioned twice in scope) are covered in T2 (backend) and T4 (frontend). i18n strings (mentioned as "UI 按钮名") are covered in T5. The 2D floor plan (mentioned only in brainstorming, not the plan brief) is covered in T8.

**Placeholder scan:** No "TBD", "fix later", or "similar to task N" remains. Each step that edits code has concrete old/new snippets OR (for audit-style steps in T6-T9) a concrete grep pattern + triage rule — the triage rule IS the content, not a placeholder.

**Type consistency:** Field names consistent throughout: `base.H` = scalar, `anchor.x/y/z` = coords, `WORLD_UP` = Three.js Vector3, `UsdGeom.Tokens.y` = USD axis enum. `slotZ` (old) → `slotY` (new) rename applied consistently in T6.

**Known weak point:** Tasks T6-T9 are audit-style (grep + triage + fix) rather than pure TDD, because the frontend has 100+ axis references and writing a failing unit test per reference is not tractable for a 7300-line monolithic HTML file. The mitigation is the browser-use screenshot verification at the end of each task, which exercises the visual invariant. Accept this trade-off.

---

**Execution:** Dispatch via superpowers:subagent-driven-development. Expect T1-T5 and T10 to be mechanical (fast). T6-T9 are frontend audits — expect iteration (reviewer will likely catch missed Z-up references that weren't in the grep cheat-sheet; that's fine, fix and re-review).
