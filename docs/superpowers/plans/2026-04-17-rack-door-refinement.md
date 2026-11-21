# Rack Perforated-Door Refinement Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-rack perforated-door refinement level, bucketed in the UI by each rack's minimum U-span (server ∪ blanking ∪ leakage), auto-detect = max across those sources; door's feature-edge level follows its surface max; excluded from the pre-mesh warning-gate dialog.

**Architecture:** Two new `meshConfig` fields (`rackDoorRefinementLevel` + `rackDoorPerMinUnitLevel`) stored bucketed by min-U-span. Frontend panel gets one new row reusing `_renderCategorySection`; auto-detect wraps the existing `_autoDetectCategory` machinery with a minUSpan-bucketing scanner and a max-aggregation rule. Backend runner resolves each rack's door level at snappyHexMeshDict generation time and rewrites two existing branches (refinement-surface + feature-edge) to use the bucket lookup.

**Tech Stack:** Python 3.10 (pydantic v2, pytest), vanilla JS (STL_generate.html), OpenFOAM snappyHexMesh.

**Spec:** `docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md`

---

## File Structure

| File | Responsibility | Create / Modify |
|------|---------------|-----------------|
| `src/backend/aircooling/runner/cfdproj_schema.py` | Declare 2 new `MeshConfig` fields with typed defaults | Modify |
| `src/backend/aircooling/runner/stlgen_runner.py` | `_compute_rack_min_u_span` helper, `_rack_door_filename_map` population, `_door_level_for_rack` resolver, update two refinement branches (line ≈3458 + line ≈3390 + line ≈3555 secondary) | Modify |
| `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` | Backend tests 1-3, 5 from spec §Testing, plus a targeted schema test | Create |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | State defaults, `scanRackDoorMinUnitBuckets`, `_autoDetectRackDoor` wired after source categories, new `_renderCategorySection` call in panel, `buildCfdExportJson` emission, preflight collector comment | Modify |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | `label.rack_door_section` + `label.rack_door_per_min_unit` | Modify |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Same keys | Modify |
| `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py` | Static-structure tests for state field + function + wiring presence (mirrors `test_perforated_frontend.py`) | Create |
| `STLGeneratorForOpenFoam/scripts/test_rack_door_runtime.py` | Runtime headless tests 4, 5, 6 via `/api/browser/exec-js` | Create |

**Conventions followed:**
- Backend tests route through `STLGenRunner` with fully-parsed `CFDExport` pydantic objects (no raw dict shortcuts) — per the *feedback_pydantic_schema_additions* memory.
- Frontend static tests use the `extract_fn_body` regex pattern from `test_perforated_frontend.py`.
- Every frontend state mutation uses `pushUndoCheckpoint` + `logOperation` — handled by existing setters (`setUnifiedRackCategory` / `setPerUnitRackCategory`) which we reuse unchanged.

---

## Task 1: Backend — Declare `rackDoorRefinementLevel` + `rackDoorPerMinUnitLevel` on MeshConfig

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:388-403`
- Create: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py`

- [ ] **Step 1.1: Write the failing schema test**

Create `src/backend/aircooling/runner/tests/test_rack_door_refinement.py`:

```python
"""Regression: rack perforated-door refinement bucketed by min-U-span.

Spec: docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
"""
from dcdtuni.runner.cfdproj_schema import CFDExport, MeshConfig


def test_meshconfig_default_rack_door_fields():
    """rackDoorRefinementLevel/rackDoorPerMinUnitLevel are declared with sane defaults."""
    mc = MeshConfig()
    assert mc.rackDoorRefinementLevel == [1, 1]
    assert mc.rackDoorPerMinUnitLevel == {}


def test_meshconfig_accepts_door_bucket_map():
    """Bucket map accepts the {str: [int, int]} shape coming from cfd_export.json."""
    mc = MeshConfig(
        rackDoorRefinementLevel=[2, 3],
        rackDoorPerMinUnitLevel={"1": [3, 4], "4": [2, 3]},
    )
    assert mc.rackDoorPerMinUnitLevel["1"] == [3, 4]
    assert mc.rackDoorPerMinUnitLevel["4"] == [2, 3]


def test_cfdexport_round_trip_door_fields():
    """Full CFDExport preserves door refinement keys through pydantic round-trip."""
    exp = CFDExport.model_validate({
        "meshConfig": {
            "rackDoorRefinementLevel": [2, 3],
            "rackDoorPerMinUnitLevel": {"2": [3, 4]},
        },
    })
    assert exp.meshConfig.rackDoorPerMinUnitLevel == {"2": [3, 4]}
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_meshconfig_default_rack_door_fields -v`
Expected: FAIL with `AttributeError: 'MeshConfig' object has no attribute 'rackDoorRefinementLevel'` (or equivalent pydantic ValidationError).

- [ ] **Step 1.3: Add the two fields to MeshConfig**

Edit `src/backend/aircooling/runner/cfdproj_schema.py` — insert two fields inside `class MeshConfig` after `baffleRefinementLevel` (around line 393), matching the existing style:

```python
class MeshConfig(CFDBaseModel):
    baseSize: float = 0.2
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    serverFaceRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    baffleRefinementLevel: List[int] = Field(default_factory=lambda: [1, 3])
    # Rack perforated-door refinement (2026-04-17 spec):
    # - rackDoorRefinementLevel: unified baseline, editable in UI
    # - rackDoorPerMinUnitLevel: bucketed by each rack's min U-span across
    #   server/blanking/leakage entries. Declared explicitly (despite
    #   extra="allow") so pydantic type-checks the value on future
    #   tightening; see maxGlobalCells comment below.
    rackDoorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackDoorPerMinUnitLevel: Dict[str, List[int]] = Field(default_factory=dict)
    partitionWallRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    partitionWallFeatureLevel: int = 2
    parallelMesh: bool = True
    meshProcess: int = 16
    maxGlobalCells: int = 5000000
    maxLocalCells: int = 5000000
```

Verify `Dict` is already imported at top of file — if not, add `from typing import Dict, List` (`List` is already used).

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v`
Expected: 3 passed.

- [ ] **Step 1.5: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "feat(schema): MeshConfig.rackDoor{Refinement,PerMinUnit}Level fields"
```

---

## Task 2: Backend — `_compute_rack_min_u_span` helper (trivial read)

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (add helper immediately below `_sanitize`)
- Modify: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` (append tests)

**Design note (2026-04-17 revision):** the backend helper does NOT
recompute spans from the rack's server/blanking/leakage lists. The
real cfdrun shape (`rack.servers[].spanSlots`, `rack.partitions[].index`,
`rack.leakage.groups[]`) is the *output* format and differs from the
front-end `state.models` shape; trying to replicate the span rule here
risks silent drift (verified on Alibaba_hangchou.cfdrun — reading
the frontend-shape keys would return `None` for every real rack).

Single source of truth: the frontend `_computeRackMinUSpan(rackModel)`
helper (added in Task 8) computes the integer once at export time and
writes it to `rack.minUSpanForDoorBucket` (Task 12). The backend
simply reads that field. If the field is absent (legacy cfdrun), the
helper returns `None` and `_door_level_for_rack` falls back to the
unified level — the same no-op behaviour as the pre-2026-04-17
baseline.

- [ ] **Step 2.1: Write failing tests for the helper**

Append to `src/backend/aircooling/runner/tests/test_rack_door_refinement.py`:

```python
from dcdtuni.runner.stlgen_runner import _compute_rack_min_u_span


def test_compute_min_u_span_reads_frontend_field():
    """Backend just reads the int the frontend already computed."""
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": 1}) == 1
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": 4}) == 4


def test_compute_min_u_span_null_field_returns_none():
    """Frontend writes null for racks with no server/blanking/leakage;
    backend treats that identically to a missing key."""
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": None}) is None


def test_compute_min_u_span_missing_field_returns_none():
    """Legacy cfdrun without the 2026-04-17 field → None → caller
    falls back to unified level."""
    assert _compute_rack_min_u_span({}) is None


def test_compute_min_u_span_string_value_is_coerced():
    """JSON round-trip through pydantic's extra='allow' may preserve
    the int as-is; accept stringified form defensively."""
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": "2"}) == 2
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v -k min_u_span`
Expected: 4 FAIL with `ImportError: cannot import name '_compute_rack_min_u_span'`.

- [ ] **Step 2.3: Implement the helper**

In `src/backend/aircooling/runner/stlgen_runner.py`, find `def _sanitize(` and
insert immediately below it (same indent level, blank line between):

```python
def _compute_rack_min_u_span(rack_data: Dict[str, Any]) -> Optional[int]:
    """Read the frontend-computed `minUSpanForDoorBucket` field.

    The frontend's `_computeRackMinUSpan(rackModel)` in STL_generate.html
    is the single source of truth for the span rule (it walks
    equipments + merged-empty-slot ranges + through-slot groups using
    the front-end state shape). At export time the computed integer
    is written to each rack as `rack.minUSpanForDoorBucket`; this
    helper just reads it.

    Returns None when the field is absent or null — legacy cfdrun
    files and racks with no categorizable entries both land here,
    and `_door_level_for_rack` falls back to the unified door level.

    See docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
    §Algorithm §Resolve and §Risks (mirror-drift mitigation).
    """
    value = rack_data.get("minUSpanForDoorBucket")
    if value is None:
        return None
    return int(value)
```

Verify `Optional` is imported at top of the file. If not, add it to the
existing `from typing import ...` line.

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v`
Expected: 7 passed.

- [ ] **Step 2.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "feat(runner): _compute_rack_min_u_span reads frontend-emitted field"
```

---

## Task 3: Backend — `_door_level_for_rack` resolver

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (add helper beside `_compute_rack_min_u_span`)
- Modify: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` (append)

- [ ] **Step 3.1: Write failing tests for the resolver**

Rack fixtures use the flat `{"minUSpanForDoorBucket": N}` shape — the
same shape Task 2's `_compute_rack_min_u_span` reads. The resolver
delegates to that helper; it does NOT walk servers/partitions/leakage
(see Task 2 design note).

Append to the test file:

```python
from dcdtuni.runner.stlgen_runner import _door_level_for_rack


def test_door_level_uses_per_bucket_value_when_min_span_matches():
    rack = {"minUSpanForDoorBucket": 1}
    mc = {
        "rackDoorRefinementLevel": [1, 1],
        "rackDoorPerMinUnitLevel": {"1": [3, 4], "4": [2, 2]},
    }
    assert _door_level_for_rack(rack, mc) == [3, 4]


def test_door_level_falls_back_to_unified_when_bucket_missing():
    """1U rack but only 4U bucket defined → unified."""
    rack = {"minUSpanForDoorBucket": 1}
    mc = {
        "rackDoorRefinementLevel": [2, 3],
        "rackDoorPerMinUnitLevel": {"4": [1, 1]},
    }
    assert _door_level_for_rack(rack, mc) == [2, 3]


def test_door_level_falls_back_to_unified_for_empty_rack():
    """Empty rack (minUSpanForDoorBucket absent) → unified, even if
    buckets exist for other spans."""
    rack = {}
    mc = {
        "rackDoorRefinementLevel": [2, 3],
        "rackDoorPerMinUnitLevel": {"1": [5, 5]},
    }
    assert _door_level_for_rack(rack, mc) == [2, 3]


def test_door_level_null_span_field_falls_back_to_unified():
    """Explicit `{"minUSpanForDoorBucket": None}` at the resolver level
    must fall back to unified, mirroring the empty-rack case."""
    rack = {"minUSpanForDoorBucket": None}
    mc = {
        "rackDoorRefinementLevel": [2, 3],
        "rackDoorPerMinUnitLevel": {"1": [5, 5]},
    }
    assert _door_level_for_rack(rack, mc) == [2, 3]


def test_door_level_defaults_when_meshconfig_missing_keys():
    """Missing both keys → [1, 1] (matches frontend initializer)."""
    rack = {"minUSpanForDoorBucket": 1}
    mc = {}
    assert _door_level_for_rack(rack, mc) == [1, 1]
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v -k door_level`
Expected: 4 FAIL with `ImportError: cannot import name '_door_level_for_rack'`.

- [ ] **Step 3.3: Implement the resolver**

Immediately below `_compute_rack_min_u_span` in `src/backend/aircooling/runner/stlgen_runner.py`:

```python
def _door_level_for_rack(rack_data: Dict[str, Any],
                        mesh_config: Dict[str, Any]) -> List[int]:
    """Resolve the surface refinement level for a single rack's doors.

    Lookup order:
      1. rackDoorPerMinUnitLevel[str(minUSpan)] if min-U-span is defined
         AND that bucket key exists.
      2. rackDoorRefinementLevel (unified).
      3. [1, 1] — matches the frontend state default so stale cfdrun
         files lacking both keys produce identical behaviour to fresh
         exports with defaults.

    See docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
    §Algorithm §Resolve.
    """
    min_span = _compute_rack_min_u_span(rack_data)
    if min_span is not None:
        bucket_map = mesh_config.get("rackDoorPerMinUnitLevel") or {}
        key = str(min_span)
        if key in bucket_map:
            return list(bucket_map[key])
    return list(mesh_config.get("rackDoorRefinementLevel") or [1, 1])
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v`
Expected: 12 passed (3 schema + 4 min-u-span + 5 resolver).

- [ ] **Step 3.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "feat(runner): _door_level_for_rack bucket resolver"
```

---

## Task 4: Backend — Build filename → rack-name reverse map & inject into snappyHexMeshDict branches

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` — 2 regions inside `_generate_snappy_hex_mesh_dict`:
  - population near line 3196 (build filename→rack reverse map)
  - refinementSurfaces branch at line 3458 (door-specific level emission)
  - (The feature-edge branch at line 3390 is Task 6. The secondary per-solid branch at line 3555 is Task 7.)
- Modify: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` (append integration test using STLGenRunner)

- [ ] **Step 4.1: Write failing integration test — bucket lookup**

Append to the test file. The test bypasses `STLGenRunner._load()` by directly
setting `_data` and `_stl_solids` — avoids having to construct a fixture cfdrun
JSON that passes pydantic validation (which would require exhaustive rack
shape). `__init__` itself only stores paths; the cfdrun file doesn't have to
exist on disk.

```python
from pathlib import Path
import pytest

from dcdtuni.runner.stlgen_runner import STLGenRunner


def _rack_stub(rack_name: str, min_span: int | None) -> dict:
    """Minimum rack shape `_generate_snappy_hex_mesh_dict` reads.

    Only fields the snappy dict generator + _compute_rack_min_u_span
    actually touch. `minUSpanForDoorBucket` is the frontend-emitted
    field the runner reads directly — no span re-derivation (see
    Task 2 design note and spec §Algorithm §Resolve). Pass `None` to
    simulate an empty rack (no server/blanking/leakage).
    """
    return {
        "minUSpanForDoorBucket": min_span,
        "inletDoor": {"enabled": True, "solidName": f"{rack_name}_inlet_door"},
        "outletDoor": {"enabled": True, "solidName": f"{rack_name}_outlet_door"},
    }


def _bootstrap_runner(tmp_path: Path, mesh_config: dict, racks: dict,
                      stl_solids: dict) -> STLGenRunner:
    """STLGenRunner with _data / _stl_solids pre-set (no _load call)."""
    (tmp_path / "Mesh" / "system").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Mesh" / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    runner = STLGenRunner(
        case_dir=tmp_path,
        cfd_export_path=tmp_path / "model" / "project.cfdrun",
    )
    runner._data = {
        "racks": racks,
        "meshConfig": mesh_config,
        "acus": {}, "baffles": {}, "floors": {}, "walls": {},
        "wallObjects": {}, "heatSources": {}, "partitionWalls": {},
        "room": {}, "simulationConfig": {},
    }
    runner._stl_solids = stl_solids
    return runner


def test_snappy_dict_emits_door_level_from_bucket(tmp_path):
    racks = {
        "R1U": _rack_stub("R1U", 1),
        "R4U": _rack_stub("R4U", 4),
    }
    stl_solids = {
        "merged_rack.stl": ["R1U", "R4U"],
        "baffle_R1U_inlet_door.stl": ["R1U_inlet_door"],
        "baffle_R1U_outlet_door.stl": ["R1U_outlet_door"],
        "baffle_R4U_inlet_door.stl": ["R4U_inlet_door"],
        "baffle_R4U_outlet_door.stl": ["R4U_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "rackDoorRefinementLevel": [1, 1],
        "rackDoorPerMinUnitLevel": {"1": [3, 4], "4": [1, 1]},
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # R1U → bucket "1" → [3, 4]
    assert 'baffle_R1U_inlet_door\n    {\n        level (3 4);' in text
    assert 'baffle_R1U_outlet_door\n    {\n        level (3 4);' in text
    # R4U → bucket "4" → [1, 1]
    assert 'baffle_R4U_inlet_door\n    {\n        level (1 1);' in text
    assert 'baffle_R4U_outlet_door\n    {\n        level (1 1);' in text
```

Note: if `_generate_snappy_hex_mesh_dict` references additional `_data` keys
or method state not set above (e.g. `self._bridge_registry`), the test fails
loudly with an AttributeError or KeyError — add the missing key to the stub
with the smallest safe default. This is the same fixture style used by
`test_partition_hole_fvoptions.py`.

- [ ] **Step 4.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_snappy_dict_emits_door_level_from_bucket -v`
Expected: FAIL — the current `baffle_` branch emits `level ({rl_baffle[0]} {rl_baffle[1]})` = `(1 1)` for all four door files (so R1U assertions fail).

- [ ] **Step 4.3: Build filename→rack-name reverse map**

In `src/backend/aircooling/runner/stlgen_runner.py`, find the existing rack-door-filename population around line 3196–3206. Replace the two-set population with a combined population that also builds the reverse map:

Before (current):

```python
        rack_door_solids: set = set()
        rack_door_filenames: set = set()
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                rack_door_solids.add(solid)
                rack_door_filenames.add(f"baffle_{solid}.stl")
```

After:

```python
        rack_door_solids: set = set()
        rack_door_filenames: set = set()
        # filename → rack_name so the refinement / feature-edge branches can
        # look up which rack a door belongs to and resolve its bucketed
        # refinement level via _door_level_for_rack. See spec §4.2.
        rack_door_filename_to_rack: Dict[str, str] = {}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                rack_door_solids.add(solid)
                fn = f"baffle_{solid}.stl"
                rack_door_filenames.add(fn)
                rack_door_filename_to_rack[fn] = rack_name
```

- [ ] **Step 4.4: Update the refinementSurfaces branch (line ≈3458)**

Still inside `_generate_snappy_hex_mesh_dict`, find the block:

```python
            if filename.startswith("baffle_"):
                # ALL baffle files (wall + perforated) use faceType internal + faceZone.
                # createBaffles then converts to wall or cyclic patches.
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue
```

Replace with a door-specific early branch **before** the generic baffle branch:

```python
            if filename in rack_door_filenames:
                # Rack perforated door: bucket-resolved level per rack's
                # min-U-span. Falls back to rackDoorRefinementLevel.
                rack_name = rack_door_filename_to_rack[filename]
                door_lvl = _door_level_for_rack(data["racks"][rack_name], mc)
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({door_lvl[0]} {door_lvl[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            if filename.startswith("baffle_"):
                # ALL baffle files (wall + perforated) use faceType internal + faceZone.
                # createBaffles then converts to wall or cyclic patches.
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue
```

- [ ] **Step 4.5: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_snappy_dict_emits_door_level_from_bucket -v`
Expected: PASS.

- [ ] **Step 4.6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "feat(runner): door refinement reads bucketed level"
```

---

## Task 5: Backend — Empty rack fallback to unified

**Files:**
- Modify: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` (append)

- [ ] **Step 5.1: Write failing test — empty rack gets unified door level**

Append to the test file:

```python
def test_snappy_dict_empty_rack_door_uses_unified(tmp_path):
    """Rack with minUSpanForDoorBucket=None → door falls back to
    rackDoorRefinementLevel, even when a per-bucket override exists
    for a bucket the rack doesn't belong to."""
    racks = {"REMPTY": _rack_stub("REMPTY", None)}  # None = empty rack
    stl_solids = {
        "merged_rack.stl": ["REMPTY"],
        "baffle_REMPTY_inlet_door.stl": ["REMPTY_inlet_door"],
        "baffle_REMPTY_outlet_door.stl": ["REMPTY_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "rackDoorRefinementLevel": [2, 3],
        "rackDoorPerMinUnitLevel": {"1": [5, 5]},  # REMPTY won't hit this
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # Empty rack → fallback to rackDoorRefinementLevel [2, 3]
    assert 'baffle_REMPTY_inlet_door\n    {\n        level (2 3);' in text
    assert 'baffle_REMPTY_outlet_door\n    {\n        level (2 3);' in text
```

- [ ] **Step 5.2: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_snappy_dict_empty_rack_door_uses_unified -v`
Expected: PASS — the Task 3 resolver already handles this correctly.

- [ ] **Step 5.3: Commit**

```bash
git add src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "test(runner): empty-rack door falls back to unified level"
```

---

## Task 6: Backend — Feature-edge level follows door surface max

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (line ≈3390 feature-edge branch)
- Modify: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` (append)

- [ ] **Step 6.1: Write failing feature-edge test**

Append:

```python
def test_snappy_dict_door_feature_edge_follows_surface_max(tmp_path):
    """Door eMesh feature level must equal the door surface max, not
    rackFeatureLevel."""
    racks = {"R1U": _rack_stub("R1U", 1)}
    stl_solids = {
        "merged_rack.stl": ["R1U"],
        "baffle_R1U_inlet_door.stl": ["R1U_inlet_door"],
        "baffle_R1U_outlet_door.stl": ["R1U_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "rackDoorRefinementLevel": [1, 1],
        "rackDoorPerMinUnitLevel": {"1": [3, 5]},
        "rackFeatureLevel": 2,   # must be overridden by door surface max
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # Door feature level = door surface max = 5 (NOT rackFeatureLevel=2)
    assert 'file "baffle_R1U_inlet_door.eMesh";\n        level 5;' in text
    assert 'file "baffle_R1U_outlet_door.eMesh";\n        level 5;' in text
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_snappy_dict_door_feature_edge_follows_surface_max -v`
Expected: FAIL — current feature-edge branch emits `level 2` (= rackFeatureLevel) for doors.

- [ ] **Step 6.3: Rewrite feature-edge branch to follow door surface max**

In `src/backend/aircooling/runner/stlgen_runner.py` find the block around line 3390:

```python
            elif filename in rack_door_filenames:
                # Rack door: inherit rack feature level (part of rack frame).
                feat_level = feat_rack
```

Replace with:

```python
            elif filename in rack_door_filenames:
                # Rack door feature edge follows its own surface max level —
                # keeps snappy feature-snap in lock-step with cell size so
                # the door outline is preserved regardless of how coarse
                # rackFeatureLevel is set. Spec §4.3.
                rack_name = rack_door_filename_to_rack[filename]
                feat_level = _door_level_for_rack(
                    data["racks"][rack_name], mc)[1]
```

- [ ] **Step 6.4: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_snappy_dict_door_feature_edge_follows_surface_max -v`
Expected: PASS.

- [ ] **Step 6.5: Run ALL Task 1-6 tests together**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v`
Expected: 13 passed (3 schema + 4 min-u-span + 4 resolver + 1 bucket integration + 1 empty-rack + 1 feature-edge).

- [ ] **Step 6.6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "feat(runner): door feature-edge level follows surface max"
```

---

## Task 7: Backend — Update the secondary per-solid door branch (line ≈3555)

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (line ≈3555 inside per-solid loop)

Context: a secondary code path emits door refinement when a door solid appears inside a multi-solid STL file (e.g. `merged_rack.stl`) rather than as a standalone `baffle_<door>.stl`. This path rarely fires in practice (doors are extracted to individual STLs earlier), but it currently uses `rl_rack` — an inconsistency we fix now to keep the invariant "all door refinement paths route through `_door_level_for_rack`".

- [ ] **Step 7.1: Locate the secondary branch**

Find in `src/backend/aircooling/runner/stlgen_runner.py`:

```python
                elif sanitized in rack_door_solids:
                    # Rack perforated door: faceType internal + faceZone at RACK
                    # refinement level (geometrically part of the rack frame,
                    # not a free-standing baffle).
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack[0]} {rl_rack[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
```

- [ ] **Step 7.2: Rewrite to use `_door_level_for_rack`**

Replace with:

```python
                elif sanitized in rack_door_solids:
                    # Rack perforated door embedded inside a multi-solid STL.
                    # Use the same bucketed door level as the standalone
                    # baffle_<door>.stl path so both routes stay consistent.
                    # Reverse-lookup rack via the door-solids→rack map built
                    # alongside rack_door_filename_to_rack.
                    rack_name = rack_door_solid_to_rack.get(sanitized)
                    if rack_name:
                        _door_lvl = _door_level_for_rack(
                            data["racks"][rack_name], mc)
                    else:
                        # Solid listed in rack_door_solids but not bound to a
                        # rack (can't happen via normal flow; guard anyway).
                        _door_lvl = mc.get("rackDoorRefinementLevel", [1, 1])
                    solid_entries.append(f"""            {solid}
            {{
                level ({_door_lvl[0]} {_door_lvl[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
```

- [ ] **Step 7.3: Populate the solid→rack sibling map**

Immediately above the refinement loop (where `rack_door_filename_to_rack` is populated — around line 3196), augment the loop body:

```python
        rack_door_solids: set = set()
        rack_door_filenames: set = set()
        rack_door_filename_to_rack: Dict[str, str] = {}
        # solid → rack_name for the secondary per-solid branch (spec §7).
        rack_door_solid_to_rack: Dict[str, str] = {}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                rack_door_solids.add(solid)
                fn = f"baffle_{solid}.stl"
                rack_door_filenames.add(fn)
                rack_door_filename_to_rack[fn] = rack_name
                rack_door_solid_to_rack[solid] = rack_name
```

- [ ] **Step 7.4: Run the full test file to confirm no regressions**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v`
Expected: 13 passed (same as before Task 7 — secondary branch change has no dedicated test because the primary path shadows it in normal flows; covered implicitly by the integration test in Task 12).

- [ ] **Step 7.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(runner): secondary door branch also uses bucketed level"
```

---

## Task 8: Frontend — State defaults + `_computeRackMinUSpan` helper

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`getInitialState()` cfdRoom block around line 2795–2828, plus a new pure helper near the other rack-scan helpers around line 24425)
- Create: `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py`

**Design note (2026-04-17 revision):** `_computeRackMinUSpan(rackModel)`
is the **single source of truth** for the min-U-span rule. Added to
the frontend helper section. Both `scanRackDoorMinUnitBuckets` (Task 9)
and `buildCfdExportJson` (Task 12) call it; the backend never re-derives
spans — it reads the int this helper produces. This design eliminates
the frontend/backend mirror drift risk the spec §Risks warns about.

- [ ] **Step 8.1: Write failing static-structure test**

Create `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py`:

```python
"""Static-structure tests for the rack-door refinement frontend wiring.

Mirrors the pattern in test_perforated_frontend.py: regex-scan
STL_generate.html for the presence of required state keys, functions,
and wiring calls. Does NOT require a running server.

Spec: docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "STL_generate.html"
HTML = HTML_PATH.read_text(encoding="utf-8")


def extract_fn_body(name):
    m = re.search(rf"function {name}\s*\([^)]*\)\s*\{{", HTML)
    if not m:
        m = re.search(rf"window\.{name}\s*=\s*function\s*\([^)]*\)\s*\{{", HTML)
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


def test_initial_state_declares_door_refinement_keys():
    body = extract_fn_body("getInitialState")
    assert body is not None, "getInitialState() not found"
    assert "rackDoorRefinementLevel" in body, \
        "getInitialState must declare rackDoorRefinementLevel"
    assert "rackDoorPerMinUnitLevel" in body, \
        "getInitialState must declare rackDoorPerMinUnitLevel"


def test_initial_state_door_refinement_defaults():
    """Defaults must match the backend MeshConfig: [1,1] / {}."""
    body = extract_fn_body("getInitialState")
    # Accept either inline or trailing-comma style
    assert re.search(r"rackDoorRefinementLevel:\s*\[\s*1\s*,\s*1\s*\]", body)
    assert re.search(r"rackDoorPerMinUnitLevel:\s*\{\s*\}", body)


def test_compute_rack_min_u_span_helper_exists():
    body = extract_fn_body("_computeRackMinUSpan")
    assert body is not None, "_computeRackMinUSpan(rackModel) must be defined"
    # Must walk the three source categories using the front-end state shape.
    assert "equipments" in body, "helper must read rack.equipments"
    assert "getRackMergedEmptySlotRanges" in body, \
        "helper must call getRackMergedEmptySlotRanges for blanking spans"
    assert "_scanThroughSlotGroups" in body, \
        "helper must call _scanThroughSlotGroups for leakage spans"
    # Must return null when no spans collected (empty-rack fallback).
    assert "null" in body or "return null" in body, \
        "helper must return null for empty racks"
```

- [ ] **Step 8.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py::test_initial_state_declares_door_refinement_keys -v`
Expected: FAIL — keys not yet declared.

- [ ] **Step 8.3: Add the two keys to getInitialState()**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, inside the `cfdRoom: { ... }` block (around line 2795–2828), add the door keys adjacent to `baffleRefinementLevel` (currently `wallBafflesRefinementLevel` at line 2817):

Find:

```javascript
                wallBafflesRefinementLevel: [1, 1],
                leakageRefinementLevel: [2, 3],
```

Insert after `wallBafflesRefinementLevel`:

```javascript
                wallBafflesRefinementLevel: [1, 1],
                // Rack perforated-door refinement (spec 2026-04-17):
                // bucketed by each rack's min-U-span; resolved per-rack at
                // export. See scanRackDoorMinUnitBuckets + _autoDetectRackDoor.
                rackDoorRefinementLevel: [1, 1],
                rackDoorPerMinUnitLevel: {},
                leakageRefinementLevel: [2, 3],
```

- [ ] **Step 8.4: Add the `_computeRackMinUSpan(rackModel)` helper**

This is the **single source of truth** for the min-U-span rule.
Task 9's `scanRackDoorMinUnitBuckets` and Task 12's
`buildCfdExportJson` both call it; the backend reads the
frontend-computed integer without re-deriving.

Find `function scanLeakageUnitBuckets()` in
`STLGeneratorForOpenFoam/web/STL_generate.html` (around line 24425)
and insert IMMEDIATELY BEFORE it:

```javascript
    // Rack min-U-span: the smallest of (server spanUnits, blanking
    // merged-empty-slot range span, leakage through-slot group span).
    // Used to bucket a rack for door-refinement lookup. Returns null
    // when the rack has no entries in any of the three categories —
    // callers treat null as "fallback to unified door level".
    //
    // This is the SINGLE SOURCE OF TRUTH for the span rule.
    // scanRackDoorMinUnitBuckets (UI) and buildCfdExportJson
    // (emits rack.minUSpanForDoorBucket) both call this. The backend
    // runner reads the emitted integer — it does NOT re-derive.
    // See docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
    // §Algorithm.
    function _computeRackMinUSpan(model) {
        if (!model || model.kind !== 'rack' || !model.rack) return null;
        var spans = [];
        (model.rack.equipments || []).forEach(function(eq) {
            if (eq && eq.spanUnits) spans.push(eq.spanUnits);
        });
        ['front', 'back'].forEach(function(side) {
            (getRackMergedEmptySlotRanges(model, side) || []).forEach(function(r) {
                spans.push(r.endUnit - r.startUnit + 1);
            });
        });
        (_scanThroughSlotGroups(model) || []).forEach(function(g) {
            spans.push(g.endUnit - g.startUnit + 1);
        });
        return spans.length ? Math.min.apply(null, spans) : null;
    }
```

- [ ] **Step 8.5: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v`
Expected: 3 passed (2 state + 1 helper).

- [ ] **Step 8.6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py
git commit -m "feat(ui): _computeRackMinUSpan helper + door state defaults"
```

---

## Task 9: Frontend — `scanRackDoorMinUnitBuckets()` scan function

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add function next to `scanLeakageUnitBuckets` around line 24425)
- Modify: `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py` (append)

- [ ] **Step 9.1: Write failing test for scan function presence**

Append to `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py`:

```python
def test_scan_rack_door_min_unit_buckets_defined():
    body = extract_fn_body("scanRackDoorMinUnitBuckets")
    assert body is not None, \
        "scanRackDoorMinUnitBuckets() must be defined"
    # Must produce { key: { count, patchHeight } } entries so the shared
    # _renderCategorySection and computeMinSafeLevel machinery works.
    assert "count" in body
    assert "patchHeight" in body
    # Must read the three source entry types on each rack.
    assert "equipments" in body
    # partitions OR getRackMergedEmptySlotRanges (both are valid
    # frontend accessors for blanking plates).
    assert "partition" in body.lower() or "getRackMergedEmptySlotRanges" in body
    # Leakage: scene uses _scanThroughSlotGroups OR direct .leakage.groups
    assert "_scanThroughSlotGroups" in body or "leakage" in body.lower()
```

- [ ] **Step 9.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py::test_scan_rack_door_min_unit_buckets_defined -v`
Expected: FAIL — function not defined.

- [ ] **Step 9.3: Add `scanRackDoorMinUnitBuckets()` alongside the existing scanners**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find `function scanLeakageUnitBuckets()` (around line 24425) and insert immediately AFTER it (before `scanRackFrameBuckets`). The scanner delegates span computation to `_computeRackMinUSpan` (Task 8) so the rule is defined in exactly one place:

```javascript
    // Rack door refinement buckets: keyed by each rack's min-U-span (from
    // _computeRackMinUSpan). Racks with zero entries are NOT bucketed
    // (their doors fall back to rackDoorRefinementLevel unified).
    // `patchHeight` is the smallest relevant height a door must resolve —
    // i.e. 1U when the rack's min-U-span is 1 — so the shared
    // computeMinSafeLevel produces a consistent safety check.
    // Spec 2026-04-17 §Algorithm.
    function scanRackDoorMinUnitBuckets() {
        var buckets = {};
        (state.models || []).forEach(function(model) {
            var minSpan = _computeRackMinUSpan(model);
            if (minSpan === null) return;
            var unitH = (model.rack && model.rack.unitHeight) || RACK_DEFAULT_UNIT_HEIGHT;
            var key = String(minSpan);
            if (!buckets[key]) {
                buckets[key] = { count: 0, patchHeight: minSpan * unitH };
            }
            buckets[key].count++;
        });
        return buckets;
    }
```

- [ ] **Step 9.4: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v`
Expected: 3 passed (2 state + 1 scan).

- [ ] **Step 9.5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py
git commit -m "feat(ui-scan): scanRackDoorMinUnitBuckets by rack min-U-span"
```

---

## Task 10: Frontend — `_autoDetectRackDoor` + wire into `autoDetectRackRefinement`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`autoDetectRackRefinement` + new `_autoDetectRackDoor` function)
- Modify: `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py` (append)

- [ ] **Step 10.1: Write failing test — auto-detect function exists and runs last**

Append:

```python
def test_auto_detect_rack_door_defined_and_wired_last():
    body = extract_fn_body("_autoDetectRackDoor")
    assert body is not None, "_autoDetectRackDoor() must be defined"

    outer = extract_fn_body("autoDetectRackRefinement")
    assert outer is not None
    # Door MUST be invoked AFTER the three source categories so it sees
    # their freshly-updated values in the same run.
    def pos(name):
        m = re.search(re.escape(name), outer)
        return m.start() if m else -1
    assert pos("_autoDetectCategory") != -1
    assert pos("_autoDetectRackDoor") != -1
    # Door call must appear after ALL _autoDetectCategory calls
    last_cat = outer.rfind("_autoDetectCategory")
    door_pos = outer.find("_autoDetectRackDoor")
    assert door_pos > last_cat, \
        "_autoDetectRackDoor must run after all _autoDetectCategory calls"


def test_auto_detect_rack_door_preserves_higher_manual_values():
    """Per-spec §Algorithm: auto-detect never lowers a user-set value."""
    body = extract_fn_body("_autoDetectRackDoor")
    assert body is not None
    # Must use Math.max over current and computed safe values — proves
    # it can only raise, never lower.
    assert "Math.max" in body
```

- [ ] **Step 10.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v -k auto_detect`
Expected: FAIL — function not defined.

- [ ] **Step 10.3: Implement `_autoDetectRackDoor()` and wire into `autoDetectRackRefinement`**

In `STLGeneratorForOpenFoam/web/STL_generate.html` find `function autoDetectRackRefinement()` (around line 24538) and add `_autoDetectRackDoor` directly above it:

```javascript
    // Rack-door auto-detect: runs AFTER server / blanking / leakage so it
    // reads the just-updated source values. For each min-U-span bucket,
    // walks the racks in that bucket and computes
    //   max(server-level, blanking-level, leakage-level)
    // across every matching entry (reading per-unit overrides first,
    // falling back to the category unified). Then Math.max with the
    // user's current bucket value so a manual bump is never undone.
    // Spec 2026-04-17 §Algorithm §Auto-detect.
    function _autoDetectRackDoor() {
        var buckets = scanRackDoorMinUnitBuckets();
        var keys = Object.keys(buckets).sort(function(a, b) { return +a - +b; });
        if (!keys.length) return [];

        var unifiedDoor = state.cfdRoom.rackDoorRefinementLevel || [1, 1];
        var perUnitDoor = state.cfdRoom.rackDoorPerMinUnitLevel || {};

        // Source category lookups
        function _levelFor(unifiedKey, perUnitKey, spanKey, fallback) {
            var perUnit = state.cfdRoom[perUnitKey] || {};
            var unified = state.cfdRoom[unifiedKey] || fallback;
            return perUnit[String(spanKey)] || unified;
        }

        var changes = [];
        keys.forEach(function(key) {
            // Walk racks in this bucket; aggregate MAX across all their
            // server / blanking / leakage entry levels.
            var entryLevels = [];
            (state.models || []).forEach(function(model) {
                if (model.kind !== 'rack' || !model.rack) return;
                // Re-compute this rack's minUSpan and filter to current bucket
                var spans = [];
                (model.rack.equipments || []).forEach(function(eq) {
                    if (eq && eq.spanUnits) spans.push(eq.spanUnits);
                });
                ['front', 'back'].forEach(function(side) {
                    (getRackMergedEmptySlotRanges(model, side) || []).forEach(function(r) {
                        spans.push(r.endUnit - r.startUnit + 1);
                    });
                });
                (_scanThroughSlotGroups(model) || []).forEach(function(g) {
                    spans.push(g.endUnit - g.startUnit + 1);
                });
                if (!spans.length) return;
                var rackMinSpan = Math.min.apply(null, spans);
                if (String(rackMinSpan) !== key) return;

                // Server entries on this rack
                (model.rack.equipments || []).forEach(function(eq) {
                    if (!eq || !eq.spanUnits) return;
                    entryLevels.push(_levelFor(
                        'serverFaceRefinementLevel', 'serverFacePerUnitLevel',
                        eq.spanUnits, [1, 1]));
                });
                // Blanking entries
                ['front', 'back'].forEach(function(side) {
                    (getRackMergedEmptySlotRanges(model, side) || []).forEach(function(r) {
                        var span = r.endUnit - r.startUnit + 1;
                        entryLevels.push(_levelFor(
                            'blankingPlateRefinementLevel',
                            'blankingPlatePerUnitLevel', span, [1, 1]));
                    });
                });
                // Leakage entries
                (_scanThroughSlotGroups(model) || []).forEach(function(g) {
                    var span = g.endUnit - g.startUnit + 1;
                    entryLevels.push(_levelFor(
                        'rackLeakageRefinementLevel',
                        'rackLeakagePerUnitLevel', span, [1, 2]));
                });
            });

            if (!entryLevels.length) return;
            var safeMin = entryLevels.reduce(function(a, l) { return Math.max(a, l[0]); }, 0);
            var safeMax = entryLevels.reduce(function(a, l) { return Math.max(a, l[1]); }, 0);
            var current = perUnitDoor[key] || unifiedDoor.slice();
            var newLevel = [Math.max(current[0], safeMin), Math.max(current[1], safeMax)];
            if (newLevel[0] !== current[0] || newLevel[1] !== current[1]) {
                changes.push({
                    uHeight: +key, count: buckets[key].count,
                    oldLevel: current.slice(), newLevel: newLevel.slice(),
                    safeLevel: [safeMin, safeMax],
                });
            }
            perUnitDoor[key] = newLevel;
        });
        // Drop stale keys for buckets no longer in the scene
        Object.keys(perUnitDoor).forEach(function(k) {
            if (!buckets[k]) delete perUnitDoor[k];
        });
        state.cfdRoom.rackDoorPerMinUnitLevel = perUnitDoor;
        return changes;
    }
```

Then modify the existing `autoDetectRackRefinement()`:

```javascript
    function autoDetectRackRefinement() {
        var frameChanges = _autoDetectRackFrame();
        var serverChanges = _autoDetectCategory(scanServerUnitBuckets, 'serverFaceRefinementLevel', 'serverFacePerUnitLevel', [1,1]);
        var blankingChanges = _autoDetectCategory(scanBlankingPlateUnitBuckets, 'blankingPlateRefinementLevel', 'blankingPlatePerUnitLevel', [1,1]);
        var leakageChanges = _autoDetectCategory(scanLeakageUnitBuckets, 'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2]);
        // Door runs LAST: reads the just-updated source levels above.
        var doorChanges = _autoDetectRackDoor();
        return {
            frame: frameChanges, server: serverChanges,
            blanking: blankingChanges, leakage: leakageChanges,
            door: doorChanges,
        };
    }
```

Also extend `runAutoDetectRackRefinement` (around line 24608) log output:

```javascript
    window.runAutoDetectRackRefinement = function() {
        pushUndoCheckpoint('autoDetectRackRefinement');
        var result = autoDetectRackRefinement();
        refreshRackRefinePanelUI();
        var allChanges = [].concat(
            (result.frame || []).map(function(c) { return '\u{1F5BC}' + c.side + ' [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }),
            result.server.map(function(c) { return '\u{1F5A5}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }),
            result.blanking.map(function(c) { return '\u{1F9F1}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }),
            result.leakage.map(function(c) { return '\u{1F4A8}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }),
            (result.door || []).map(function(c) { return '\u{1F6AA}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; })
        );
        if (allChanges.length) {
            logOperation('autoDetectRackRefinement', t('log.auto_adjust_refinement', { changes: allChanges.join(', ') }), { result: result }, 'modify');
        }
    };
```

- [ ] **Step 10.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v`
Expected: 5 passed.

- [ ] **Step 10.5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py
git commit -m "feat(ui-autodetect): _autoDetectRackDoor runs after source cats"
```

---

## Task 11: Frontend — UI row + i18n labels

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`refreshRackRefinePanelUI` around line 24677)
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`
- Modify: `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py` (append)

- [ ] **Step 11.1: Write failing test — panel renders door row**

Append:

```python
def test_refresh_panel_calls_door_category_section():
    body = extract_fn_body("refreshRackRefinePanelUI")
    assert body is not None
    # Panel must invoke _renderCategorySection with the door state keys.
    assert "rackDoorRefinementLevel" in body
    assert "rackDoorPerMinUnitLevel" in body
    assert "scanRackDoorMinUnitBuckets" in body


def test_i18n_has_rack_door_label_keys():
    import json
    zh = json.loads((HTML_PATH.parent / "i18n" / "zh-CN.json").read_text())
    en = json.loads((HTML_PATH.parent / "i18n" / "en-US.json").read_text())
    assert "label.rack_door_section" in zh
    assert "label.rack_door_per_min_unit" in zh
    assert "label.rack_door_section" in en
    assert "label.rack_door_per_min_unit" in en
    # Per-min-unit label must interpolate {n} and {count}
    assert "{n}" in zh["label.rack_door_per_min_unit"]
    assert "{count}" in zh["label.rack_door_per_min_unit"]
```

- [ ] **Step 11.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v -k "panel or i18n"`
Expected: FAIL — panel doesn't mention door state keys; i18n keys absent.

- [ ] **Step 11.3: Add i18n label keys**

Edit `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` — add two keys (alphabetically near other `label.rack_*`):

```json
"label.rack_door_section": "🚪 穿孔门 / Perforated Door",
"label.rack_door_per_min_unit": "{n}U min-span ({count})",
```

Edit `STLGeneratorForOpenFoam/web/i18n/en-US.json`:

```json
"label.rack_door_section": "🚪 Perforated Door",
"label.rack_door_per_min_unit": "{n}U min-span ({count})",
```

- [ ] **Step 11.4: Add the `_renderCategorySection` call for doors**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find `refreshRackRefinePanelUI` (around line 24677). It currently concatenates four sections. Add a fifth AFTER the leakage section:

Find:

```javascript
            + _renderCategorySection(
                t('label.rack_leakage'), scanLeakageUnitBuckets,
                'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2],
                'label.leakage_per_unit',
                "window.setUnifiedRackCategory('rackLeakageRefinementLevel','rackLeakagePerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('rackLeakagePerUnitLevel','rackLeakageRefinementLevel','{KEY}',{IDX},+this.value)"
            );
    }
```

Replace the trailing `);` with `)` and append a new section, then close:

```javascript
            + _renderCategorySection(
                t('label.rack_leakage'), scanLeakageUnitBuckets,
                'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2],
                'label.leakage_per_unit',
                "window.setUnifiedRackCategory('rackLeakageRefinementLevel','rackLeakagePerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('rackLeakagePerUnitLevel','rackLeakageRefinementLevel','{KEY}',{IDX},+this.value)"
            )
            // Rack-door row (spec 2026-04-17): bucketed by each rack's
            // min-U-span. Reuses the shared setters; no new state machinery.
            + _renderCategorySection(
                t('label.rack_door_section'), scanRackDoorMinUnitBuckets,
                'rackDoorRefinementLevel', 'rackDoorPerMinUnitLevel', [1,1],
                'label.rack_door_per_min_unit',
                "window.setUnifiedRackCategory('rackDoorRefinementLevel','rackDoorPerMinUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('rackDoorPerMinUnitLevel','rackDoorRefinementLevel','{KEY}',{IDX},+this.value)"
            );
    }
```

- [ ] **Step 11.5: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v`
Expected: 7 passed.

- [ ] **Step 11.6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py
git commit -m "feat(ui-panel): rack door refinement row with bucket counts"
```

---

## Task 12: Frontend — `buildCfdExportJson` emits door fields

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `buildCfdExportJson` `meshConfig` block (around line 27315)
  - `buildCfdExportJson` per-rack loop (for the new per-rack
    `minUSpanForDoorBucket` integer). Find the block that iterates
    `state.models.filter(kind==='rack')` and builds each rack's
    export entry.
- Modify: `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py` (append)

**Design note (2026-04-17 revision):** besides the global meshConfig
keys, this task ALSO emits a per-rack `minUSpanForDoorBucket: int|null`
field so the backend `_compute_rack_min_u_span` (Task 2) can read it
directly — no span re-derivation server-side.

- [ ] **Step 12.1: Write failing test — export emits door keys + per-rack field**

Append:

```python
def test_build_cfd_export_json_emits_door_fields():
    body = extract_fn_body("buildCfdExportJson")
    assert body is not None
    # meshConfig block must carry the two global door keys so the
    # backend MeshConfig pydantic model receives them.
    assert "rackDoorRefinementLevel" in body
    assert "rackDoorPerMinUnitLevel" in body
    # Per-rack loop must call _computeRackMinUSpan and emit the result
    # as rack.minUSpanForDoorBucket — this is the single key the
    # backend reads to bucket each rack's door level.
    assert "minUSpanForDoorBucket" in body, \
        "each rack export must carry minUSpanForDoorBucket"
    assert "_computeRackMinUSpan" in body, \
        "the per-rack emit must call _computeRackMinUSpan — the single " \
        "source of truth for the span rule"
```

- [ ] **Step 12.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py::test_build_cfd_export_json_emits_door_fields -v`
Expected: FAIL.

- [ ] **Step 12.3: Emit the two global keys**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find `buildCfdExportJson` → `meshConfig: {` (around line 27315). Adjacent to `baffleRefinementLevel`/`leakageRefinementLevel`, add:

Find:

```javascript
                baffleRefinementLevel: state.cfdRoom.wallBafflesRefinementLevel || [1, 1],
                leakageRefinementLevel: state.cfdRoom.leakageRefinementLevel || [2, 3],
```

Insert BEFORE `baffleRefinementLevel` line:

```javascript
                rackDoorRefinementLevel: state.cfdRoom.rackDoorRefinementLevel || [1, 1],
                rackDoorPerMinUnitLevel: state.cfdRoom.rackDoorPerMinUnitLevel || {},
                baffleRefinementLevel: state.cfdRoom.wallBafflesRefinementLevel || [1, 1],
                leakageRefinementLevel: state.cfdRoom.leakageRefinementLevel || [2, 3],
```

- [ ] **Step 12.4: Emit per-rack `minUSpanForDoorBucket`**

Still inside `buildCfdExportJson`, find the per-rack export loop. It builds each rack's entry with keys like `stlFile`, `geometry`, `servers`, `partitions`, `leakage`, `inletDoor`, `outletDoor`, etc. Locate the line that assigns the rack object to `racks[rack.name]` or similar — just before that assignment (where the rack entry object is being built), add:

```javascript
        // 2026-04-17: per-rack min-U-span pre-computed here so the
        // backend runner reads a single int instead of re-deriving from
        // servers/partitions/leakage. Single source of truth — see
        // _computeRackMinUSpan docstring.
        rackEntry.minUSpanForDoorBucket = _computeRackMinUSpan(rack);
```

Where `rackEntry` is the local variable holding the built entry dict and `rack` is the `state.models` rack being iterated. Use the exact names present in the surrounding code — check before inserting. If the entry is built inline (object-literal returned from a map/filter), refactor to introduce a `rackEntry` temporary and set the field on it.

- [ ] **Step 12.5: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v`
Expected: 8 passed.

- [ ] **Step 12.6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py
git commit -m "feat(ui-export): emit door levels + per-rack minUSpanForDoorBucket"
```

---

## Task 13: Frontend — Confirm door is excluded from preflight gate

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add an explicit comment + unchanged behaviour at `collectRackSurfaceRefinementWarnings` line ≈24568)
- Modify: `STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py` (append)

The spec requires that `collectRackSurfaceRefinementWarnings()` NOT emit door entries. By not adding a door call to the concat chain (and not having added one in Task 10), this is already true — but we lock in the invariant with an explicit test + code comment.

- [ ] **Step 13.1: Write failing test — collector excludes door**

Append:

```python
def test_collect_warnings_does_not_reference_door_state():
    body = extract_fn_body("collectRackSurfaceRefinementWarnings")
    assert body is not None
    # Door state keys MUST NOT appear here: the pre-mesh dialog feeds
    # from this collector; door is intentionally not a blocker. Spec §Preflight.
    assert "rackDoorRefinementLevel" not in body, \
        "collectRackSurfaceRefinementWarnings must NOT reference door state " \
        "(doors are excluded from the mesh-gen warning dialog per spec)"
    assert "rackDoorPerMinUnitLevel" not in body
    assert "scanRackDoorMinUnitBuckets" not in body
```

- [ ] **Step 13.2: Run test**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py::test_collect_warnings_does_not_reference_door_state -v`
Expected: PASS (already satisfied — nothing added in Task 10).

- [ ] **Step 13.3: Add a code comment making the exclusion explicit**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find `function collectRackSurfaceRefinementWarnings()` (around line 24568). Insert a comment block ABOVE the function body:

```javascript
    // NOTE: door refinement category is INTENTIONALLY EXCLUDED from this
    // collector. Door level is downstream of server / blanking / leakage
    // (auto-detect derives it) and a "too low" door value is an explicit
    // user cost-capping choice, not a silent correctness hazard. The panel
    // itself still visually marks low door levels via _renderCategorySection's
    // per-row safety icon; this function only gates the pre-mesh dialog.
    // Spec 2026-04-17 §Preflight.
    function collectRackSurfaceRefinementWarnings() {
```

- [ ] **Step 13.4: Re-run the whole frontend test file**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py -v`
Expected: 9 passed.

- [ ] **Step 13.5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/scripts/test_rack_door_frontend.py
git commit -m "docs(ui): mark collectRackSurfaceRefinementWarnings as door-exempt"
```

---

## Task 14: Runtime verification via headless browser

**Files:**
- Create: `STLGeneratorForOpenFoam/scripts/test_rack_door_runtime.py`

This task exercises the full frontend pipeline against a running server — covers spec tests 4, 5, 6. Runs AFTER Tasks 1–13. Requires the server running with the loopback-auth bypass (established in today's earlier session).

- [ ] **Step 14.1: Verify the server is running**

```bash
lsof -i :8005 2>/dev/null | head
```

Expected: a python process listening on 8005. If not, start it:

```bash
conda run -n dcdtuni --no-capture-output python STLGeneratorForOpenFoam/server/server.py \
    --port 8005 --ws-port 8006 \
    --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case/alibaba-hongchou &
```

- [ ] **Step 14.2: Write the runtime test file**

Create `STLGeneratorForOpenFoam/scripts/test_rack_door_runtime.py`:

```python
"""Runtime behaviour tests for rack-door refinement.

Drives the headless browser owned by server.py via /api/browser/exec-js.
Covers spec tests 4 (auto-detect), 5 (preserve override), 6 (preflight
exclusion). Requires the STL generator server running on localhost:8005
with admin auth (admin / WIN#@desk) — see CLAUDE.md.
"""
import json
import os
import subprocess

import pytest

BASE = "http://localhost:8005"
COOKIE_JAR = "/tmp/stlgen_cookie_rack_door_runtime.txt"


def _login():
    subprocess.run([
        "curl", "-s", "-c", COOKIE_JAR, "-X", "POST",
        f"{BASE}/api/auth/login",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"username": "admin", "password": "WIN#@desk"}),
    ], check=True, capture_output=True)


def _exec_js(js):
    r = subprocess.run([
        "curl", "-s", "-b", COOKIE_JAR, "-X", "POST",
        f"{BASE}/api/browser/exec-js",
        "-H", "Content-Type: application/json",
        "--max-time", "30",
        "-d", json.dumps({"js": js}),
    ], check=True, capture_output=True, text=True)
    return json.loads(r.stdout)


def _load_proj(path):
    r = subprocess.run([
        "curl", "-s", "-b", COOKIE_JAR, "-X", "POST",
        f"{BASE}/api/browser/load-proj",
        "-H", "Content-Type: application/json",
        "--max-time", "300",
        "-d", json.dumps({"path": path}),
    ], check=True, capture_output=True, text=True)
    return json.loads(r.stdout)


@pytest.fixture(scope="module", autouse=True)
def _session():
    _login()
    yield


# NB: uses the same test cfdproj as the main alibaba-hongchou run — its
# rack set spans 1U and 4U, enough to populate two buckets. If/when a
# dedicated fixture cfdproj exists, swap this path.
FIXTURE_CFDPROJ = (
    "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/"
    "projects/Alibaba_hangchou.cfdproj"
)


def test_auto_detect_fills_door_bucket_to_max_over_sources():
    """Spec test 4: door bucket ≥ max source level after auto-detect."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    # Set source categories to known values so we can assert the max.
    _exec_js("""
        state.cfdRoom.serverFaceRefinementLevel = [2, 3];
        state.cfdRoom.serverFacePerUnitLevel = {};
        state.cfdRoom.blankingPlateRefinementLevel = [1, 2];
        state.cfdRoom.blankingPlatePerUnitLevel = {};
        state.cfdRoom.rackLeakageRefinementLevel = [1, 2];
        state.cfdRoom.rackLeakagePerUnitLevel = {};
        state.cfdRoom.rackDoorRefinementLevel = [1, 1];
        state.cfdRoom.rackDoorPerMinUnitLevel = {};
        window.runAutoDetectRackRefinement();
    """)
    got = _exec_js(
        "JSON.stringify(state.cfdRoom.rackDoorPerMinUnitLevel)"
    )["result"]
    per_bucket = json.loads(got)
    assert per_bucket, "door per-min-unit map must be populated"
    # Every populated bucket must be >= [2, 3] (max of sources)
    for key, lvl in per_bucket.items():
        assert lvl[0] >= 2, f"bucket {key} min={lvl[0]} < 2"
        assert lvl[1] >= 3, f"bucket {key} max={lvl[1]} < 3"


def test_auto_detect_preserves_user_higher_manual_value():
    """Spec test 5: manual [5,6] override on bucket '1' must NOT be lowered."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    _exec_js("""
        state.cfdRoom.serverFaceRefinementLevel = [2, 3];
        state.cfdRoom.blankingPlateRefinementLevel = [1, 1];
        state.cfdRoom.rackLeakageRefinementLevel = [1, 1];
        state.cfdRoom.rackDoorPerMinUnitLevel = {'1': [5, 6]};
        window.runAutoDetectRackRefinement();
    """)
    got = _exec_js(
        "JSON.stringify(state.cfdRoom.rackDoorPerMinUnitLevel['1'] || null)"
    )["result"]
    lvl = json.loads(got)
    assert lvl is not None, "bucket '1' must still exist"
    assert lvl == [5, 6], f"manual override was not preserved: {lvl}"


def test_door_low_level_alone_does_not_trigger_preflight_dialog():
    """Spec test 6a: door below safe must NOT fire the warning dialog."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    _exec_js("""
        state.cfdRoom.serverFaceRefinementLevel = [3, 4];
        state.cfdRoom.serverFacePerUnitLevel = {};
        state.cfdRoom.blankingPlateRefinementLevel = [3, 4];
        state.cfdRoom.blankingPlatePerUnitLevel = {};
        state.cfdRoom.rackLeakageRefinementLevel = [3, 4];
        state.cfdRoom.rackLeakagePerUnitLevel = {};
        // Door intentionally too low:
        state.cfdRoom.rackDoorRefinementLevel = [1, 1];
        state.cfdRoom.rackDoorPerMinUnitLevel = {};
    """)
    count = _exec_js(
        "collectRackSurfaceRefinementWarnings().length"
    )["result"]
    assert count == 0, (
        "collectRackSurfaceRefinementWarnings returned "
        f"{count} warnings — door must be excluded"
    )


@pytest.mark.parametrize("cat_key,peruser_key,fallback_low", [
    ("serverFaceRefinementLevel", "serverFacePerUnitLevel", [1, 1]),
    ("blankingPlateRefinementLevel", "blankingPlatePerUnitLevel", [1, 1]),
    ("rackLeakageRefinementLevel", "rackLeakagePerUnitLevel", [1, 1]),
])
def test_each_source_category_low_DOES_trigger_preflight_dialog(
    cat_key, peruser_key, fallback_low,
):
    """Spec test 6b: dropping ANY of server/blanking/leakage still fires."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    # All source levels high except the one we're testing
    js = f"""
        state.cfdRoom.serverFaceRefinementLevel = [3, 4];
        state.cfdRoom.serverFacePerUnitLevel = {{}};
        state.cfdRoom.blankingPlateRefinementLevel = [3, 4];
        state.cfdRoom.blankingPlatePerUnitLevel = {{}};
        state.cfdRoom.rackLeakageRefinementLevel = [3, 4];
        state.cfdRoom.rackLeakagePerUnitLevel = {{}};
        // Drop only the target:
        state.cfdRoom.{cat_key} = {json.dumps(fallback_low)};
        state.cfdRoom.{peruser_key} = {{}};
    """
    _exec_js(js)
    count = _exec_js(
        "collectRackSurfaceRefinementWarnings().length"
    )["result"]
    assert count > 0, (
        f"{cat_key} dropped to {fallback_low} — dialog should fire but did not"
    )
```

- [ ] **Step 14.3: Run the runtime tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_rack_door_runtime.py -v`
Expected: 3 passed + 3 parametrized (6 total) passed.

- [ ] **Step 14.4: Commit**

```bash
git add STLGeneratorForOpenFoam/scripts/test_rack_door_runtime.py
git commit -m "test(runtime): spec tests 4/5/6 via headless browser exec-js"
```

---

## Task 15: End-to-end mesh verification on alibaba-hongchou

This task validates the whole stack on a real case — spec §Rollout note "Mesh regeneration is required". Not a unit test; a manual validation step you run once at the end.

- [ ] **Step 15.1: Reload the project and run auto-detect**

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/load-proj \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Alibaba_hangchou.cfdproj"}'

curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/exec-js \
  -H "Content-Type: application/json" \
  -d '{"js": "window.runAutoDetectRackRefinement(); JSON.stringify(state.cfdRoom.rackDoorPerMinUnitLevel)"}'
```

Expected: a non-empty JSON map (e.g. `{"1":[2,3],"4":[2,3]}`), shaped by whatever the three source categories got auto-detected to.

- [ ] **Step 15.2: Kick off mesh generation with the new levels**

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath": "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Alibaba_hangchou.cfdproj", "meshOnly": true}' \
  --max-time 600
```

Expected: a `jobId` back. Note it.

- [ ] **Step 15.3: Poll until complete**

```bash
# Substitute <jobId>
while true; do
  r=$(curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/job-status?job_id=<jobId>")
  echo "$r"
  echo "$r" | grep -q '"done": true' && break
  sleep 30
done
```

Expected: `"done": true, "returncode": 0` within ~30 min.

- [ ] **Step 15.4: Sanity-check the generated snappyHexMeshDict**

```bash
grep -A2 "baffle_.*_door" /mnt/lv/litianyu/mycode/dcdtuni/case/alibaba-hongchou/Mesh/system/snappyHexMeshDict | head -n 20
```

Expected: door surface levels are `(3 4)` or similar — NOT `(1 1)`. Verify at least two distinct levels if the scene has multiple min-U-span buckets.

- [ ] **Step 15.5: Verify no non-manifold edges regression via checkMesh**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/case/alibaba-hongchou/Mesh && \
  conda run -n dcdtuni --no-capture-output checkMesh -case . 2>&1 | \
  grep -E "multiply connected|non-orthogonality|Min volume"
```

Expected: "multiply connected" edge count on door patches is much lower than the ≈138 reported pre-fix; `Min volume` is an order of magnitude larger than the 3e-9 m³ baseline.

- [ ] **Step 15.6: Document observed numbers in the commit message**

```bash
git commit --allow-empty -m "chore(e2e): verify rack door refinement on alibaba-hongchou

Auto-detected bucket map: <paste map>
Pre-fix: min volume ~3e-9, non-manifold edges ~138/door
Post-fix: min volume <X>, non-manifold edges <Y>/door
checkMesh: <pass/skew faces count>
"
```

---

## Self-Review

**1. Spec coverage:** every spec section maps to at least one task.

| Spec section | Task(s) |
|--------------|---------|
| §UX panel row | 11 |
| §UX preflight policy | 13 + 14 |
| §Data model | 1 (schema) + 8 (state) |
| §Algorithm `_computeRackMinUSpan` | 2 (backend) + 9 (frontend scan) |
| §Algorithm auto-detect | 10 + 14 |
| §Algorithm resolve | 3 + 4 |
| §Frontend state defaults | 8 |
| §Frontend scan | 9 |
| §Frontend auto-detect | 10 |
| §Frontend UI row | 11 |
| §Frontend warnings exclusion | 13 + 14 |
| §Frontend export | 12 |
| §Frontend normalize | 8 (defaults are safe on legacy projects via `\|\| [1,1]`) |
| §Backend schema | 1 |
| §Backend runner helpers | 2 + 3 + 4 |
| §Backend refinement branch | 4 |
| §Backend feature-edge branch | 6 |
| §Backend secondary branch | 7 |
| §Testing tests 1–6 | Tasks 1 (t1), 4 (t1 bucket), 5 (t2 fallback), 6 (t3 feature), 14 (t4,5,6 runtime) |
| §Rollout | 15 |
| §Risks — minUSpan frontend/backend drift | mitigation via doc-cross-references in Tasks 2 + 9 helpers |
| §Risks — cell count | noted in Task 15 expected output |
| §Risks — legacy cases | out of scope for this plan (spec marks it as a UX hint, YAGNI until user requests) |

**2. Placeholder scan:** no TBD/TODO; every code block is complete; every shell command has expected output described.

**3. Type consistency:**
- `rackDoorRefinementLevel` always `List[int]` / `[int, int]` ✓
- `rackDoorPerMinUnitLevel` keys always `str(int)`, values always `[int, int]` ✓
- `_door_level_for_rack` returns `List[int]` of length 2 everywhere it's called ✓
- `_compute_rack_min_u_span` returns `Optional[int]`; callers (`_door_level_for_rack`, tests) handle None explicitly ✓
- Frontend `buckets[key] = {count, patchHeight}` shape matches what `_renderCategorySection`, `computeMinSafeLevel`, and `_autoDetectCategory`-style code expect ✓
- Frontend `state.cfdRoom.rackDoorPerMinUnitLevel[key]` is always `[min, max]` / length-2 ✓
