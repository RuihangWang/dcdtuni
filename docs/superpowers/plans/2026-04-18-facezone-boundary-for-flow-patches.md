# faceZone-Protected Boundary for Flow-Through Patches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Force snappyHexMesh to preserve fanServer `_inlet_patch`, fanServer `_outlet_*`, ACU supply / return, and rack-level aggregated inlet/outlet solids by appending `faceType boundary; faceZone {sanitized};` to their `refinementSurfaces` region entries.

**Architecture:** Three-line surgical edit to `src/backend/aircooling/runner/stlgen_runner.py` (same two-line append applied to three existing `elif` branches that emit `patchInfo { type patch; }` for flow-through solids). Plus one new pytest file with ≥6 structural tests, all reusing the `_bootstrap_runner` + `_make_dummy_stl` harness from `test_rack_door_refinement.py`.

**Tech Stack:** Python 3.10, pytest, conda env `dcdtuni`. Runner-side only — NO frontend, NO STL generation, NO BC code touched.

**Spec:** [docs/superpowers/specs/2026-04-18-facezone-boundary-for-flow-patches-design.md](../specs/2026-04-18-facezone-boundary-for-flow-patches-design.md)

---

## File Structure

**New files:**

- `src/backend/aircooling/runner/tests/test_facezone_boundary_for_flow_patches.py` — 7 structural tests asserting that each of 5 flow-through categories gets the two new lines, and that wall-type solids do NOT.

**Modified files:**

- `src/backend/aircooling/runner/stlgen_runner.py` — three `elif` branches inside `_generate_snappy_hex_mesh_dict` (around L3914, L3939, L3950). Each branch's `solid_entries.append(...)` gets two new lines appended inside its f-string.

**Not touched (spec §2.3 hard no-goes):**

- `src/backend/aircooling/runner/fields.py`
- `src/backend/aircooling/runner/bridge_bc.py`
- `STLGeneratorForOpenFoam/web/**` (no frontend changes)
- `STLGeneratorForOpenFoam/src/**` (no STL generator changes)
- `src/backend/aircooling/runner/tests/test_field_strict_flag.py` mesh-path placeholder fallback — the placeholder stays as the last safety net per spec §4.1.

---

## Task 1: Scaffold test file with shared harness

**Files:**
- Create: `src/backend/aircooling/runner/tests/test_facezone_boundary_for_flow_patches.py`

- [ ] **Step 1: Write the failing test file**

The harness mirrors the one in `test_rack_door_refinement.py:117-184`. Each test bootstraps a minimal runner, invokes `_generate_snappy_hex_mesh_dict`, reads the file, asserts the generated text contains the two new lines for each affected solid.

```python
"""Structural tests for the 2026-04-18 faceZone-boundary fix.

Spec: docs/superpowers/specs/2026-04-18-facezone-boundary-for-flow-patches-design.md

Appends `faceType boundary; faceZone {sanitized};` to three existing
elif branches in stlgen_runner._generate_snappy_hex_mesh_dict (L3914,
L3939, L3950) so snappyHexMesh unconditionally preserves flow-through
`type=patch` solids that would otherwise be silently merged into
higher-refinement neighbor surfaces when cell-count pressure hits.
"""
from pathlib import Path

from dcdtuni.runner.stlgen_runner import STLGenRunner


def _make_dummy_stl(solid_name: str) -> str:
    """Return a minimal valid ASCII STL with the given solid name."""
    return (
        f"solid {solid_name}\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        f"endsolid {solid_name}\n"
    )


def _bootstrap_runner(tmp_path: Path, mesh_config: dict, data_overrides: dict,
                      stl_solids: dict) -> STLGenRunner:
    """STLGenRunner with _data / _stl_solids pre-set (no _load call)."""
    tri_surface = tmp_path / "Mesh" / "constant" / "triSurface"
    (tmp_path / "Mesh" / "system").mkdir(parents=True, exist_ok=True)
    tri_surface.mkdir(parents=True, exist_ok=True)
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    for filename, solids in stl_solids.items():
        content = "".join(_make_dummy_stl(s) for s in solids)
        (tri_surface / filename).write_text(content, encoding="utf-8")
    runner = STLGenRunner(
        case_dir=tmp_path,
        cfdrun_path=tmp_path / "model" / "project.cfdrun",
    )
    base = {
        "racks": {}, "acus": {}, "baffles": {}, "floors": {}, "walls": {},
        "wallObjects": {}, "heatSources": {}, "partitionWalls": {},
        "room": {}, "simulationConfig": {},
        "meshConfig": mesh_config,
    }
    base.update(data_overrides)
    runner._data = base
    runner._stl_solids = stl_solids
    runner._build_locations_in_mesh_entries = lambda: []
    return runner


def _fanserver_rack(rack_name: str, span: int,
                    inlet_solid: str, outlet_solid: str) -> dict:
    """Rack with ONE fanServer, integral-face inlet (no patch, no wallSolid),
    outlet in port-patch mode (matches the common CMI-China shape)."""
    outlet_port = outlet_solid + "_p0"
    outlet_wall = outlet_solid + "_wall"
    return {
        rack_name: {
            "servers": [{
                "type": "fanServer",
                "spanSlots": span,
                "faces": {
                    "inlet":  {"solid": inlet_solid,  "patches": [], "wallSolid": None},
                    "outlet": {"solid": None, "patches": [{"solid": outlet_port}],
                               "wallSolid": outlet_wall},
                    "top":    {"solid": f"{rack_name}_top",    "patches": [], "wallSolid": None},
                    "bottom": {"solid": f"{rack_name}_bottom", "patches": [], "wallSolid": None},
                    "left":   {"solid": f"{rack_name}_left",   "patches": [], "wallSolid": None},
                    "right":  {"solid": f"{rack_name}_right",  "patches": [], "wallSolid": None},
                },
            }],
        },
    }


def _acu(acu_id: str, supply_solids: list, return_solids: list) -> dict:
    return {
        acu_id: {
            "supplyFaces": [{"solidName": s} for s in supply_solids],
            "returnFaces": [{"solidName": r} for r in return_solids],
        },
    }


def _snappy_text(runner: STLGenRunner) -> str:
    system_dir = runner.case_dir / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    return (system_dir / "snappyHexMeshDict").read_text()


def _has_facezone_entry(text: str, solid: str) -> bool:
    """Assert both `patchInfo { type patch; }` AND
    `faceType boundary;` AND `faceZone {solid};` appear for this solid's
    regions block. Order matters: inside the same region entry."""
    # Quick-and-dirty: all three markers appear within 200 chars of the solid name.
    idx = text.find(solid + "\n")
    if idx < 0:
        return False
    block = text[idx:idx + 600]
    return ("type patch" in block
            and "faceType boundary" in block
            and f"faceZone {solid}" in block)


# ---------- Category 1: server_inlet_solids / server_outlet_solids ----------

def test_fanserver_integral_inlet_gets_facezone_boundary(tmp_path):
    """Integral-face inlet (no patches, no wallSolid — 'whole front face'
    mode used by base_leakage and CMI-China). This is the exact failure
    case — the design MUST give it a faceZone."""
    racks = _fanserver_rack("B01", span=14,
                            inlet_solid="B01_srv_inlet_patch",
                            outlet_solid="B01_srv_outlet")
    stl_solids = {
        "merged_rack.stl": ["B01"],
        "merged_server_14U.stl": [
            "B01_srv_inlet_patch", "B01_srv_outlet_p0", "B01_srv_outlet_wall",
            "B01_top", "B01_bottom", "B01_left", "B01_right",
        ],
    }
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"racks": racks}, stl_solids)
    text = _snappy_text(runner)
    assert _has_facezone_entry(text, "B01_srv_inlet_patch"), \
        f"integral inlet missing faceZone boundary — excerpt:\n{text[text.find('B01_srv_inlet_patch'):][:400]}"


def test_fanserver_outlet_port_gets_facezone_boundary(tmp_path):
    """Port-patch outlet ('_p0'). Same treatment as inlet."""
    racks = _fanserver_rack("B01", span=14,
                            inlet_solid="B01_srv_inlet_patch",
                            outlet_solid="B01_srv_outlet")
    stl_solids = {
        "merged_rack.stl": ["B01"],
        "merged_server_14U.stl": [
            "B01_srv_inlet_patch", "B01_srv_outlet_p0", "B01_srv_outlet_wall",
            "B01_top", "B01_bottom", "B01_left", "B01_right",
        ],
    }
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"racks": racks}, stl_solids)
    text = _snappy_text(runner)
    assert _has_facezone_entry(text, "B01_srv_outlet_p0"), \
        "port-patch outlet missing faceZone boundary"


# ---------- Category 2: acu_supply_solids / acu_return_solids ----------

def test_acu_supply_gets_facezone_boundary(tmp_path):
    acus = _acu("ACU1",
                supply_solids=["ACU1_Front", "ACU1_Side_patch_1"],
                return_solids=["ACU1_Back"])
    stl_solids = {"merged_acu.stl": ["ACU1_Front", "ACU1_Side_patch_1", "ACU1_Back"]}
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"acus": acus}, stl_solids)
    text = _snappy_text(runner)
    assert _has_facezone_entry(text, "ACU1_Front"), "ACU supply missing faceZone"
    assert _has_facezone_entry(text, "ACU1_Side_patch_1"), "ACU supply (named patch) missing faceZone"


def test_acu_return_gets_facezone_boundary(tmp_path):
    acus = _acu("ACU1",
                supply_solids=["ACU1_Front"],
                return_solids=["ACU1_Back", "ACU1_Side_return_patch"])
    stl_solids = {"merged_acu.stl": ["ACU1_Front", "ACU1_Back", "ACU1_Side_return_patch"]}
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"acus": acus}, stl_solids)
    text = _snappy_text(runner)
    assert _has_facezone_entry(text, "ACU1_Back"), "ACU return missing faceZone"
    assert _has_facezone_entry(text, "ACU1_Side_return_patch"), "ACU return (named patch) missing faceZone"


# ---------- Category 3: rack_io_solids ----------

def test_rack_level_aggregated_inlet_gets_facezone_boundary(tmp_path):
    """Rack-level aggregated `_inlet_uX_uY` (rack_io_solids). Pattern
    `_(inlet|outlet)_(safe_|frame_|u\\d)` in stlgen_runner.py:3485 matches
    these names."""
    racks = {
        "A04a": {"servers": []},  # no fanServer, just rack structure
    }
    stl_solids = {
        "merged_rack.stl": [
            "A04a",
            "A04a_inlet_u1",
            "A04a_inlet_u34_u44",
            "A04a_outlet_u1",
        ],
    }
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"racks": racks}, stl_solids)
    text = _snappy_text(runner)
    assert _has_facezone_entry(text, "A04a_inlet_u1"), "rack aggregated inlet_u1 missing faceZone"
    assert _has_facezone_entry(text, "A04a_inlet_u34_u44"), "rack aggregated inlet_uX_uY missing faceZone"
    assert _has_facezone_entry(text, "A04a_outlet_u1"), "rack aggregated outlet missing faceZone"


# ---------- Negative case: wall solids do NOT get faceZone boundary ----------

def test_wall_solids_do_NOT_get_facezone_boundary(tmp_path):
    """Scope guard: the fix applies only to flow-through patch solids.
    Rack frame, server top/bottom/left/right, outlet_wall — all type=wall
    today — must stay as `patchInfo { type wall; inGroups (wall); }`
    with NO faceType/faceZone lines."""
    racks = _fanserver_rack("B01", span=14,
                            inlet_solid="B01_srv_inlet_patch",
                            outlet_solid="B01_srv_outlet")
    racks["B01"]["frameSolidNames"] = ["B01_frame"]
    stl_solids = {
        "merged_rack.stl": ["B01", "B01_frame"],
        "merged_server_14U.stl": [
            "B01_srv_inlet_patch", "B01_srv_outlet_p0", "B01_srv_outlet_wall",
            "B01_top", "B01_bottom", "B01_left", "B01_right",
        ],
    }
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"racks": racks}, stl_solids)
    text = _snappy_text(runner)

    # These are type=wall — must NOT have faceType boundary.
    for wall_solid in (
        "B01_frame",
        "B01_top", "B01_bottom", "B01_left", "B01_right",
        "B01_srv_outlet_wall",
    ):
        idx = text.find(wall_solid + "\n")
        assert idx >= 0, f"{wall_solid} missing from dict entirely"
        block = text[idx:idx + 400]
        assert "type wall" in block, f"{wall_solid} expected type wall, got: {block}"
        assert "faceType boundary" not in block, \
            f"{wall_solid} must NOT have faceType boundary (scope guard)"
        assert f"faceZone {wall_solid}" not in block, \
            f"{wall_solid} must NOT have faceZone line"


def test_through_zone_solid_does_NOT_get_facezone_boundary(tmp_path):
    """Another scope guard: through-zone / leakage solids are handled by
    a SEPARATE elif branch (L3906) that also emits type wall. Must not
    leak into the fix."""
    racks = {
        "R1": {
            "servers": [],
            "leakage": {
                "groups": [{
                    "stlSolidName": "R1_leakage_group",
                    "sigma": 0.05,
                    "startUnit": 1, "endUnit": 2,
                }],
            },
        },
    }
    stl_solids = {"merged_rack.stl": ["R1", "R1_leakage_group"]}
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.2}, {"racks": racks}, stl_solids)
    text = _snappy_text(runner)
    idx = text.find("R1_leakage_group\n")
    if idx >= 0:
        block = text[idx:idx + 400]
        assert "faceType boundary" not in block, \
            "through-zone solid must NOT get faceZone boundary (L3906 branch, not in scope)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_facezone_boundary_for_flow_patches.py -v
```

Expected: 5 failures and 2 passes. Specifically:

- `test_fanserver_integral_inlet_gets_facezone_boundary` — **FAIL** (assertion: faceType boundary missing)
- `test_fanserver_outlet_port_gets_facezone_boundary` — **FAIL**
- `test_acu_supply_gets_facezone_boundary` — **FAIL**
- `test_acu_return_gets_facezone_boundary` — **FAIL**
- `test_rack_level_aggregated_inlet_gets_facezone_boundary` — **FAIL**
- `test_wall_solids_do_NOT_get_facezone_boundary` — **PASS** (already passes; scope guard)
- `test_through_zone_solid_does_NOT_get_facezone_boundary` — **PASS** (already passes; scope guard)

If the 5 "should fail" tests already pass, something is wrong — stop and investigate.

- [ ] **Step 3: Commit the failing tests**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add src/backend/aircooling/runner/tests/test_facezone_boundary_for_flow_patches.py
git commit -m "test(runner): structural tests for faceZone-boundary on flow-through patches

Fails by design until the runner fix lands. Covers:
- fanServer integral-face inlet (whole-face mode)
- fanServer outlet port (_p0)
- ACU supply + return (including named sub-patches)
- rack-level aggregated inlet/outlet (_inlet_uX, _outlet_uY)
- negative scope: frame / server top-bottom-left-right / outlet_wall / through-zone
  must NOT get the new lines"
```

---

## Task 2: Apply the two-line append to the three elif branches

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` L3914–L3955

- [ ] **Step 1: Edit the `server_inlet_solids | server_outlet_solids` branch (L3914)**

Find this block in `_generate_snappy_hex_mesh_dict`:

```python
                elif sanitized in server_inlet_solids or sanitized in server_outlet_solids:
                    # Per-unit refinement: look up this solid's spanSlots → bucket level
                    _span = solid_to_span.get(sanitized)
                    _rl = per_unit_levels.get(str(_span), rl_server) if _span else rl_server
                    if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                        _rl = rl_server
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl[0]} {_rl[1]});
                patchInfo {{ type patch; }}
            }}""")
```

Replace the `solid_entries.append(...)` block with (two new lines appended — `faceType boundary;` and `faceZone {sanitized};` — matching the indentation of sibling `level`/`patchInfo` lines inside the f-string):

```python
                elif sanitized in server_inlet_solids or sanitized in server_outlet_solids:
                    # Per-unit refinement: look up this solid's spanSlots → bucket level
                    _span = solid_to_span.get(sanitized)
                    _rl = per_unit_levels.get(str(_span), rl_server) if _span else rl_server
                    if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                        _rl = rl_server
                    # 2026-04-18: flow-through patch solids get a named
                    # faceZone so snappy preserves them unconditionally,
                    # independent of neighbor refinement pressure. See
                    # docs/superpowers/specs/2026-04-18-facezone-boundary-for-flow-patches-design.md
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl[0]} {_rl[1]});
                patchInfo {{ type patch; }}
                faceType boundary;
                faceZone {sanitized};
            }}""")
```

- [ ] **Step 2: Edit the `rack_io_solids` branch (L3939)**

Find this block:

```python
                elif sanitized in rack_io_solids:
                    # Blanking plate (wall patch): per-unit refinement
                    _span_bp = solid_to_blanking_span.get(sanitized)
                    _rl_bp = per_unit_blanking.get(str(_span_bp), rl_blanking_unified) if _span_bp else rl_rack
                    if not isinstance(_rl_bp, (list, tuple)) or len(_rl_bp) < 2:
                        _rl_bp = rl_rack
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl_bp[0]} {_rl_bp[1]});
                patchInfo {{ type patch; }}
            }}""")
```

Replace with (note: leave the existing `# Blanking plate (wall patch)` comment — it's misleading but pre-existing and out of scope to reword; just append the two lines):

```python
                elif sanitized in rack_io_solids:
                    # Blanking plate (wall patch): per-unit refinement
                    _span_bp = solid_to_blanking_span.get(sanitized)
                    _rl_bp = per_unit_blanking.get(str(_span_bp), rl_blanking_unified) if _span_bp else rl_rack
                    if not isinstance(_rl_bp, (list, tuple)) or len(_rl_bp) < 2:
                        _rl_bp = rl_rack
                    # 2026-04-18: see note on server_inlet_solids branch above.
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl_bp[0]} {_rl_bp[1]});
                patchInfo {{ type patch; }}
                faceType boundary;
                faceZone {sanitized};
            }}""")
```

- [ ] **Step 3: Edit the `acu_supply_solids | acu_return_solids` branch (L3950)**

Find this block:

```python
                elif sanitized in acu_supply_solids or sanitized in acu_return_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_acu[0]} {rl_acu[1]});
                patchInfo {{ type patch; }}
            }}""")
```

Replace with:

```python
                elif sanitized in acu_supply_solids or sanitized in acu_return_solids:
                    # 2026-04-18: see note on server_inlet_solids branch above.
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_acu[0]} {rl_acu[1]});
                patchInfo {{ type patch; }}
                faceType boundary;
                faceZone {sanitized};
            }}""")
```

- [ ] **Step 4: Run the Task 1 tests and verify they now pass**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_facezone_boundary_for_flow_patches.py -v
```

Expected: **7 passed, 0 failed**.

If any of the 5 "should-now-pass" tests still fail, the append landed in the wrong branch or the f-string indentation drifted. Re-read your diff against the block templates above.

- [ ] **Step 5: Run the full runner test suite to catch regressions**

Run:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ 2>&1 | tail -15
```

Expected: same pass/fail count as before plus 7 new passes. The 4 pre-existing `test_partition_hole_fvoptions.py` failures (commit `1584926`, 2026-04-15) remain — they are unrelated to this change. No other regressions should appear.

- [ ] **Step 6: Commit the fix**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "fix(runner): faceZone-protect flow-through patch solids in snappyDict

snappyHexMesh was silently dropping fanServer _inlet_patch solids in
large cases with tight maxGlobalCells cap (CMI-China: 6/15 inlets
survived). Root cause: integral-face inlets have no wall-sibling
solid, so when cell-count pressure hits, snappy merges the type=patch
face into a higher-refinement neighbor surface.

Fix: append 'faceType boundary; faceZone {sanitized};' to the three
existing refinementSurfaces elif branches that emit
patchInfo { type patch; } for flow-through solids:
  - server_inlet_solids / server_outlet_solids (L3914)
  - rack_io_solids (L3939)
  - acu_supply_solids / acu_return_solids (L3950)

snappy preserves faceZone-named faces unconditionally. BC generation
in fields.py is unchanged; the face is still type=patch in
polyMesh/boundary.

Spec: docs/superpowers/specs/2026-04-18-facezone-boundary-for-flow-patches-design.md"
```

---

## Task 3: E2E regression on CMI-China (manual verification)

**Files:** none modified — verification only.

This task confirms the fix actually rescues the real failure case. Skip if no OpenFOAM environment is available on the current machine — the structural tests in Task 1-2 are the formal gate; this is a smoke test.

- [ ] **Step 1: Start the local server**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
fuser -k 8025/tcp 2>/dev/null; fuser -k 8026/tcp 2>/dev/null
sleep 1
nohup conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py \
    --case-dir case/CMI-China --port 8025 --ws-port 8026 \
    > /tmp/stlgen-srv.log 2>&1 &
disown
sleep 4
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8025/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"WIN#@desk"}' | head -1
```

Expected: `{"ok": true, "token": "...", "username": "admin", "role": "admin"}`.

- [ ] **Step 2: Trigger mesh generation via REST API**

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8025/api/run-mesh \
    -H "Content-Type: application/json" \
    -d '{"meshOnly":true}' 2>&1
```

Expected: `{"jobId": "XXXXXXXX"}`. Capture the jobId.

- [ ] **Step 3: Poll until mesh job completes**

```bash
JOB=<jobId-from-step-2>
until [ "$(curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8025/api/job-status?job_id=$JOB" \
              | grep -oE '"done":[^,}]+' | cut -d: -f2)" = "true" ]; do
  curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8025/api/job-status?job_id=$JOB"
  echo ""
  sleep 30
done
curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8025/api/job-status?job_id=$JOB"
```

Expected: job reaches `"done": true`, `"returncode": 0`. Time: 5–20 minutes depending on hardware.

- [ ] **Step 4: Count fanServer inlets in the final boundary file**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
INLETS=$(grep -cE "_inlet_patch$" case/CMI-China/Mesh/constant/polyMesh/boundary)
OUTLETS=$(grep -cE "_outlet_p0$" case/CMI-China/Mesh/constant/polyMesh/boundary)
echo "_inlet_patch: $INLETS (expected: 15)"
echo "_outlet_p0:   $OUTLETS (expected: 15)"
```

Expected: **both counts = 15**. Previously `INLETS` was 6 (commit `e16c073` baseline). If `INLETS < 15`, the fix did not fully rescue the case — capture the boundary diff vs commit `e16c073` for follow-up investigation.

- [ ] **Step 5: Trigger solve and confirm no inlet-missing RuntimeError**

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8025/api/run-mesh \
    -H "Content-Type: application/json" \
    -d '{"solveOnly":true}' 2>&1
# Capture the new jobId, poll until done
# Check that the log does NOT contain:
#   "RuntimeError: fan_outlet '...': paired inlet patch '...' is missing"
```

Expected: solve reaches its first iteration without the `paired inlet patch missing` RuntimeError from `fields.py:687`. If the error still fires for any patch, that patch survived the old code path (faceZone did not cover it) — stop and extend the test harness in Task 1 with its shape.

- [ ] **Step 6: Regression check on base_leakage**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
fuser -k 8025/tcp 2>/dev/null; fuser -k 8026/tcp 2>/dev/null
sleep 1
nohup conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py \
    --case-dir case/base_leakage --port 8025 --ws-port 8026 \
    > /tmp/stlgen-srv.log 2>&1 &
disown
sleep 4
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8025/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"WIN#@desk"}' > /dev/null
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8025/api/run-mesh \
    -H "Content-Type: application/json" \
    -d '{"meshOnly":true}' 2>&1
# Poll until done, then:
grep -cE "_inlet_patch$" case/base_leakage/Mesh/constant/polyMesh/boundary
```

Expected: **count = 4** (base_leakage has 4 fanServers, all integral-face inlet mode; was 4 before, still 4 after — no regression).

- [ ] **Step 7: Teardown + summary**

```bash
fuser -k 8025/tcp 2>/dev/null; fuser -k 8026/tcp 2>/dev/null
```

Document the results (inlet counts for both cases, solve pass/fail for CMI-China) in the commit message for Task 4 or in a comment in the PR description.

---

## Task 4: Update (or explicitly keep) the mesh-placeholder safety-net test

**Files:**
- Possibly modify: `src/backend/aircooling/runner/tests/test_field_strict_flag.py` — documentation of the safety-net's scope post-fix.

- [ ] **Step 1: Confirm `test_field_strict_flag.py` still passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_field_strict_flag.py -v
```

Expected: all 8 tests pass. The mesh-path placeholder fallback (from commit `a4bb864`) is unaffected by this fix — it lives in `fields.py._write_t_field` and triggers only when a paired inlet is missing, which faceZone now prevents in the common case but the safety-net remains for unforeseen geometries.

- [ ] **Step 2: No code change needed**

Spec §4.1 explicitly says the placeholder stays in place as the last safety net. No edit in this task — just confirm the existing tests still green. Skip the commit step.

---

## Task 5: Final regression sweep + ship

**Files:** none modified — final verification only.

- [ ] **Step 1: Full runner + frontend structural test sweep**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest \
    src/backend/aircooling/runner/tests/ \
    STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py \
    STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py \
    2>&1 | tail -5
```

Expected: `N passed, 4 failed` where 4 = the pre-existing `test_partition_hole_fvoptions.py` failures (unchanged from baseline).

- [ ] **Step 2: Node-side tests (if environment available)**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
node --test scripts/test_patch_filter.mjs scripts/test_patch_selection.mjs 2>&1 | tail -5
```

Expected: `24 pass` (unchanged from baseline — this fix does not touch any frontend JS).

- [ ] **Step 3: Confirm commit history**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git log --oneline | head -5
```

Expected: top commits show the two commits from Tasks 1 and 2 (test file add + runner fix). No submodule pointer bump is needed — the fix is entirely in the parent repo runner.

- [ ] **Step 4: Push readiness (optional — user-gated)**

Do NOT push without user consent. When user authorises:

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git push origin main
```

---

## Self-Review Notes

- **Spec coverage:**
  - §1 Problem / Motivation → Tasks 1-2 tests reproduce the integral-face-inlet and port-patch shapes that fail today.
  - §2.1 Affected categories → Tasks 1 tests cover all 5 (server inlet, server outlet port, acu supply, acu return, rack aggregated). Task 2 edits the exact three branches feeding those categories.
  - §2.2 NOT affected → Task 1 Step 6-7 negative tests (`test_wall_solids_do_NOT_...`, `test_through_zone_solid_does_NOT_...`).
  - §3 Architecture → Task 2 edits are at the exact lines called out in spec (L3914, L3939, L3950) with the exact template.
  - §3.3 faceZone naming convention (bare sanitized name) → `_has_facezone_entry` asserts exactly `faceZone {solid}`, not `faceZone {solid}_faceZone`.
  - §4.1 mesh-path placeholder preserved → Task 4 confirms `test_field_strict_flag.py` still green without change.
  - §7.1 ≥6 tests → 7 tests land (5 positive + 2 negative scope guards).
  - §7.2 E2E regression → Task 3 (manual, OpenFOAM-dependent).
  - §10 Acceptance criteria 1 (unit tests) → Tasks 1+2. Criteria 2-3 (CMI-China) → Task 3 Steps 4-5. Criteria 4 (base_leakage regression) → Task 3 Step 6. Criteria 5 (placeholder not triggered) → Task 3 Step 5 grep. Criteria 6 (no cross-cutting changes) → Task 5 Step 1-2.
- **Placeholder scan:** no "TBD", "add appropriate error handling", or "similar to Task N" — every step gives the full block or the exact command.
- **Type consistency:** the assertion helper `_has_facezone_entry(text, solid)` and the naming `faceZone {sanitized}` match across all tests and across the three edits in Task 2. Sanitization is a no-op for all test inputs (alphanumeric + underscore).
