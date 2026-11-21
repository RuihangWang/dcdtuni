# Baffle STL Merge Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge 477 individual baffle/door STL files into ~3–5 files, reducing snappyHexMeshDict from 107K lines to ~5K and mesh time from 57 min to < 10 min.

**Architecture:** Wall baffles merge into one file with `faceType baffle` (skips createBaffles). Rack doors merge into one file with `faceType internal` + createBaffles (cyclic needed for porousBafflePressure). Individual perforated baffles/floors keep separate files. Per-bucket door refinement is removed — all doors share one unified level. Frontend validates uniform door porosity.

**Tech Stack:** Python 3.10 (stlgen_runner.py, cfdproj_schema.py), JavaScript (STL_generate.html), OpenFOAM 2406 (snappyHexMesh, createBaffles), pytest.

**Test case:** `Base_leakage` (4 racks, 2 doors, 3 wall baffles, perforated floor — small but covers all categories).

---

### Task 1: Remove per-bucket door fields from MeshConfig schema

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:420-429`
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:85-130` (door level helpers)
- Test: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py`

- [ ] **Step 1: Read existing door refinement test file**

Read `src/backend/aircooling/runner/tests/test_rack_door_refinement.py` fully to understand current test coverage and patterns.

- [ ] **Step 2: Write failing test — unified door level (no per-bucket)**

In `src/backend/aircooling/runner/tests/test_rack_door_refinement.py`, add:

```python
def test_door_level_unified_no_bucket():
    """After merge, _door_level_for_rack ignores per-bucket and uses unified level."""
    rack_data = {"minUSpanForDoorBucket": 2}
    mesh_config = {
        "rackDoorRefinementLevel": [3, 4],
        # per-bucket should be ignored even if present
        "rackDoorPerMinUnitLevel": {"2": [5, 6]},
    }
    result = _door_level_for_rack(rack_data, mesh_config)
    assert result == [3, 4], f"Expected unified [3,4], got {result}"


def test_door_feature_level_unified_no_bucket():
    """After merge, _door_feature_level_for_rack uses unified level only."""
    rack_data = {"minUSpanForDoorBucket": 2}
    mesh_config = {
        "rackDoorFeatureLevel": 3,
        "rackDoorRefinementLevel": [2, 4],
        # per-bucket should be ignored
        "rackDoorFeaturePerMinUnitLevel": {"2": 6},
    }
    result = _door_feature_level_for_rack(rack_data, mesh_config)
    assert result == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py::test_door_level_unified_no_bucket -v`
Expected: FAIL — per-bucket `{"2": [5, 6]}` takes priority, returns `[5, 6]` instead of `[3, 4]`.

- [ ] **Step 4: Simplify `_door_level_for_rack` and `_door_feature_level_for_rack`**

In `src/backend/aircooling/runner/stlgen_runner.py`, replace `_door_level_for_rack` (lines ~85–105):

```python
def _door_level_for_rack(rack_data: Dict[str, Any], mesh_config: Dict[str, Any]) -> List[int]:
    """Unified door refinement level — no per-bucket variation.
    All doors share one level since they merge into a single STL file."""
    return list(mesh_config.get("rackDoorRefinementLevel") or [1, 1])
```

Replace `_door_feature_level_for_rack` (lines ~108–130):

```python
def _door_feature_level_for_rack(rack_data: Dict[str, Any],
                                 mesh_config: Dict[str, Any]) -> int:
    """Unified door feature-line level. Falls back to surface max."""
    explicit = mesh_config.get("rackDoorFeatureLevel")
    if explicit is not None:
        return int(explicit)
    rl = mesh_config.get("rackDoorRefinementLevel") or [1, 1]
    return int(rl[-1]) if rl else 1
```

- [ ] **Step 5: Remove per-bucket door fields from MeshConfig**

In `src/backend/aircooling/runner/cfdproj_schema.py`, remove these two fields from `MeshConfig`:

```python
# DELETE these lines:
rackDoorPerMinUnitLevel: Dict[str, List[int]] = Field(default_factory=dict)
rackDoorFeaturePerMinUnitLevel: Dict[str, int] = Field(default_factory=dict)
```

Keep: `rackDoorRefinementLevel` and `rackDoorFeatureLevel`.

- [ ] **Step 6: Remove "door" from `_BUCKET_FILE_PATTERNS`**

In `src/backend/aircooling/runner/stlgen_runner.py`, remove the `"door"` entry from `_BUCKET_FILE_PATTERNS` (line ~140):

```python
# DELETE this line from the dict:
"door":        ("merged_door_",        "rackDoorRefinementLevel",      "rackDoorPerMinUnitLevel",   None,                        None),
```

This means `_parse_bucket_from_filename("merged_door_2U.stl")` will now return `None` — the file won't be recognized as a bucket file. The runner will skip it during normal bucket processing; the new merge logic (Task 2) handles doors directly.

- [ ] **Step 7: Run tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_rack_door_refinement.py -v`
Expected: ALL PASS.

Also run full test suite to check for regressions:
Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v --tb=short`

- [ ] **Step 8: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_rack_door_refinement.py
git commit -m "refactor(schema): remove per-bucket door refinement, unify to single level"
```

---

### Task 2: Merge wall baffles + doors in `_split_internal_face_solids()`

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:1949-2098` (`_split_internal_face_solids`)
- Create: `src/backend/aircooling/runner/tests/test_baffle_merge.py`

- [ ] **Step 1: Write failing test — wall baffles merge into one file**

Create `src/backend/aircooling/runner/tests/test_baffle_merge.py`:

```python
"""Tests for baffle STL merge optimization.

Uses Base_leakage case to verify that wall baffles merge into
merged_wall_baffle.stl and rack doors merge into merged_rack_door.stl.
"""
import json
import re
from pathlib import Path

import pytest

from dcdtuni.runner.stlgen_runner import STLGenRunner

BASE_LEAKAGE_DIR = Path(__file__).resolve().parents[4] / "case" / "base_leakage"
CFDRUN = BASE_LEAKAGE_DIR / "model" / "Base_leakage.cfdrun"


@pytest.fixture
def runner():
    """Load Base_leakage runner (does NOT run mesh — just _load + _split)."""
    if not CFDRUN.exists():
        pytest.skip(f"Base_leakage cfdrun not found: {CFDRUN}")
    r = STLGenRunner(BASE_LEAKAGE_DIR, CFDRUN)
    r._load()
    return r


def test_wall_baffles_merged(runner):
    """All wall baffles should be in merged_wall_baffle.stl, not individual files."""
    stl_files = runner._stl_solids

    # merged_wall_baffle.stl must exist
    assert "merged_wall_baffle.stl" in stl_files, \
        f"merged_wall_baffle.stl not found. Files: {list(stl_files.keys())}"

    # Individual wall baffle files should NOT exist
    wall_individuals = [f for f in stl_files if f.startswith("baffle_")
                        and "perforated" not in f.lower()
                        and "_door" not in f]
    assert wall_individuals == [], \
        f"Individual wall baffle files should be merged: {wall_individuals}"


def test_rack_doors_merged(runner):
    """All rack doors should be in merged_rack_door.stl, not individual/bucket files."""
    stl_files = runner._stl_solids

    # merged_rack_door.stl must exist (Base_leakage has doors on Rack_4)
    with open(CFDRUN) as f:
        data = json.load(f)
    has_doors = any(
        r.get(f"{side}Door", {}).get("enabled", False)
        for r in data.get("racks", {}).values()
        for side in ("inlet", "outlet")
    )
    if has_doors:
        assert "merged_rack_door.stl" in stl_files, \
            f"merged_rack_door.stl not found. Files: {list(stl_files.keys())}"

    # No individual door files or merged_door_*U.stl bucket files
    door_files = [f for f in stl_files
                  if "_door" in f and f != "merged_rack_door.stl"]
    assert door_files == [], \
        f"Individual/bucket door files should be merged: {door_files}"


def test_perforated_baffles_stay_individual(runner):
    """Perforated baffle models and perforated floor patches stay as individual files."""
    stl_files = runner._stl_solids

    # Base_leakage has Room_Raising_floor_patch (perforated floor)
    perf_files = [f for f in stl_files if f.startswith("perforated_")]
    assert len(perf_files) >= 1, \
        f"Expected at least 1 individual perforated file, got: {perf_files}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py -v`
Expected: FAIL — `merged_wall_baffle.stl` not found, individual `baffle_*.stl` files exist.

- [ ] **Step 3: Implement wall baffle + door merge in `_split_internal_face_solids()`**

In `src/backend/aircooling/runner/stlgen_runner.py`, rewrite `_split_internal_face_solids()` (lines 1949–2098). The key changes:

1. Categorize solids into 4 groups: `wall_baffle_solids`, `door_solids`, `perforated_baffle_solids`, `perforated_floor_solids`
2. Merge `wall_baffle_solids` → `merged_wall_baffle.stl`
3. Merge `door_solids` → `merged_rack_door.stl`
4. Extract `perforated_baffle_solids` → individual `perforated_baffle_<name>.stl`
5. Extract `perforated_floor_solids` → individual `perforated_floor_<name>.stl`

```python
def _split_internal_face_solids(self) -> None:
    """Merge/extract solids that need faceType internal or baffle into
    category-specific STL files.

    - Wall baffles (all types) → merged_wall_baffle.stl (faceType baffle)
    - Rack doors → merged_rack_door.stl (faceType internal + createBaffles cyclic)
    - Perforated baffles → individual perforated_baffle_<name>.stl
    - Perforated floors → individual perforated_floor_<name>.stl
    """
    data = self._data

    # --- Categorize solids by physical behavior ---
    # {sanitized_name: source_stl_filename}
    wall_baffle_solids: Dict[str, str] = {}
    door_solids: Dict[str, str] = {}
    perforated_baffle_solids: Dict[str, str] = {}
    perforated_floor_solids: Dict[str, str] = {}

    # 1. Floor solids: wallBaffle remaining-area → wall; perforated openings → individual
    fc_key = "merged_floor_ceiling.stl"
    for floor_name, floor in data.get("floors", {}).items():
        default_bc = floor.get("defaultBC", "wall")
        san_fn = _sanitize(floor_name)
        op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
        if default_bc == "wallBaffle":
            for s in self._stl_solids.get(fc_key, []):
                san = _sanitize(s)
                if san.startswith(san_fn) and san not in op_names:
                    wall_baffle_solids[san] = fc_key
        for op in floor.get("openings", []):
            op_type = op.get("physics", {}).get("type", "wall")
            if op_type in ("perforated", "wallBaffle"):
                san = _sanitize(op["solidName"])
                perforated_floor_solids[san] = fc_key

    # 2. Rack wallBaffle solids (half-dissolved U-slots) → wall
    for filename, solids in list(self._stl_solids.items()):
        bucket = _parse_bucket_from_filename(filename)
        if bucket and bucket[0] == "wallBaffle":
            for solid in solids:
                wall_baffle_solids[_sanitize(solid)] = filename

    # 3. ALL baffle model solids → classify by physics
    bf_key = "merged_baffle.stl"
    for solid in self._stl_solids.get(bf_key, []):
        san = _sanitize(solid)
        phys = self._baffle_physics.get(san, {})
        porosity = phys.get("porosity", 0)
        baffle_type = phys.get("baffleType", "wall")
        if porosity > 0 or baffle_type == "perforated":
            perforated_baffle_solids[san] = bf_key
        else:
            wall_baffle_solids[san] = bf_key

    # 4. Perforated solids from merged_perforated.stl → individual
    perf_key = "merged_perforated.stl"
    for solid in self._stl_solids.get(perf_key, []):
        perforated_floor_solids[_sanitize(solid)] = perf_key

    # 5. Rack perforated doors → merged_rack_door.stl
    for filename, solids in list(self._stl_solids.items()):
        bucket = _parse_bucket_from_filename(filename)
        if bucket and bucket[0] == "door":
            for solid in solids:
                door_solids[_sanitize(solid)] = filename

    # Also check merged_door_*.stl files that don't match bucket pattern
    # (e.g. if "door" was removed from _BUCKET_FILE_PATTERNS)
    for filename in list(self._stl_solids.keys()):
        if filename.startswith("merged_door_") and filename.endswith(".stl"):
            for solid in self._stl_solids[filename]:
                san = _sanitize(solid)
                if san not in door_solids:
                    door_solids[san] = filename

    all_needs_extract = {}
    all_needs_extract.update(wall_baffle_solids)
    all_needs_extract.update(door_solids)
    all_needs_extract.update(perforated_baffle_solids)
    all_needs_extract.update(perforated_floor_solids)

    if not all_needs_extract:
        self._discard_or_keep_legacy_through_zone_stl()
        return

    # --- Read STL blocks from source files ---
    stl_blocks: Dict[str, Dict[str, str]] = {}
    for filename in set(all_needs_extract.values()):
        fpath = self.tri_surface_dir / filename
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            stl_text = f.read()
        blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                            stl_text, re.MULTILINE | re.DOTALL)
        stl_blocks[filename] = {}
        for block, name in blocks:
            stl_blocks[filename][_sanitize(name)] = block

    # --- Write merged wall baffle file ---
    if wall_baffle_solids:
        merged_text = ""
        merged_names = []
        for san, src_file in wall_baffle_solids.items():
            if src_file in stl_blocks and san in stl_blocks[src_file]:
                merged_text += stl_blocks[src_file][san] + "\n"
                orig_name = san
                for s in self._stl_solids.get(src_file, []):
                    if _sanitize(s) == san:
                        orig_name = s
                        break
                merged_names.append(orig_name)
        if merged_text:
            out_path = self.tri_surface_dir / "merged_wall_baffle.stl"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(merged_text)
            self._stl_solids["merged_wall_baffle.stl"] = merged_names
            logger.info(f"Merged {len(merged_names)} wall baffles → merged_wall_baffle.stl")

    # --- Write merged rack door file ---
    if door_solids:
        merged_text = ""
        merged_names = []
        for san, src_file in door_solids.items():
            if src_file in stl_blocks and san in stl_blocks[src_file]:
                merged_text += stl_blocks[src_file][san] + "\n"
                orig_name = san
                for s in self._stl_solids.get(src_file, []):
                    if _sanitize(s) == san:
                        orig_name = s
                        break
                merged_names.append(orig_name)
        if merged_text:
            out_path = self.tri_surface_dir / "merged_rack_door.stl"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(merged_text)
            self._stl_solids["merged_rack_door.stl"] = merged_names
            logger.info(f"Merged {len(merged_names)} rack doors → merged_rack_door.stl")

    # --- Write individual perforated files ---
    for category, prefix in [
        (perforated_baffle_solids, "perforated_baffle_"),
        (perforated_floor_solids, "perforated_floor_"),
    ]:
        for san, src_file in category.items():
            if src_file not in stl_blocks or san not in stl_blocks[src_file]:
                continue
            block_text = stl_blocks[src_file][san]
            ind_file = f"{prefix}{san}.stl"
            ind_path = self.tri_surface_dir / ind_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block_text + "\n")
            orig_name = san
            for s in self._stl_solids.get(src_file, []):
                if _sanitize(s) == san:
                    orig_name = s
                    break
            self._stl_solids[ind_file] = [orig_name]
            logger.info(f"Extracted {san} → {ind_file}")

    # --- Remove extracted solids from source files ---
    extracted_from: Dict[str, set] = {}
    for san, src_file in all_needs_extract.items():
        extracted_from.setdefault(src_file, set()).add(san)

    for filename, extracted_names in extracted_from.items():
        fpath = self.tri_surface_dir / filename
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            stl_text = f.read()
        blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                            stl_text, re.MULTILINE | re.DOTALL)
        keep_text = ""
        keep_names = []
        for block, name in blocks:
            if _sanitize(name) not in extracted_names:
                keep_text += block + "\n"
                keep_names.append(name)
        if keep_names:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(keep_text)
            self._stl_solids[filename] = keep_names
        else:
            del self._stl_solids[filename]
            fpath.unlink()
        logger.info(f"Updated {filename}: {len(keep_names)} solids remaining")

    self._discard_or_keep_legacy_through_zone_stl()
```

- [ ] **Step 4: Run tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_baffle_merge.py
git commit -m "feat(runner): merge wall baffles + doors into grouped STL files"
```

---

### Task 3: Update snappyHexMeshDict generation for merged files

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:3502-4200` (`_generate_snappy_hex_mesh_dict`)
- Test: `src/backend/aircooling/runner/tests/test_baffle_merge.py`

- [ ] **Step 1: Write failing test**

Add to `test_baffle_merge.py`:

```python
def test_snappy_dict_merged_wall_baffle_entry(runner):
    """snappyHexMeshDict should have one merged_wall_baffle entry with faceType baffle."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()

    snappy_path = BASE_LEAKAGE_DIR / "Mesh" / "system" / "snappyHexMeshDict"
    text = snappy_path.read_text()

    # merged_wall_baffle in geometry (no regions)
    assert "merged_wall_baffle.stl" in text
    assert re.search(r'merged_wall_baffle\.stl\s*\{\s*type\s+triSurfaceMesh;\s*name\s+merged_wall_baffle;\s*\}', text), \
        "merged_wall_baffle geometry should have no regions block"

    # faceType baffle in refinementSurfaces
    assert re.search(r'merged_wall_baffle\s*\{[^}]*faceType\s+baffle;', text), \
        "merged_wall_baffle should use faceType baffle"

    # No individual baffle_ entries (except perforated_baffle_ / perforated_floor_)
    individual_baffles = re.findall(r'^\s+baffle_\S+', text, re.MULTILINE)
    assert individual_baffles == [], \
        f"No individual baffle_ entries expected: {individual_baffles}"


def test_snappy_dict_merged_rack_door_entry(runner):
    """snappyHexMeshDict should have one merged_rack_door entry with faceType internal."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()

    snappy_path = BASE_LEAKAGE_DIR / "Mesh" / "system" / "snappyHexMeshDict"
    text = snappy_path.read_text()

    # Check if Base_leakage has doors
    import json
    with open(CFDRUN) as f:
        data = json.load(f)
    has_doors = any(
        r.get(f"{side}Door", {}).get("enabled", False)
        for r in data.get("racks", {}).values()
        for side in ("inlet", "outlet")
    )
    if not has_doors:
        pytest.skip("No doors in Base_leakage")

    # merged_rack_door in geometry
    assert "merged_rack_door.stl" in text

    # faceType internal (NOT baffle) — needs cyclic for porousBafflePressure
    assert re.search(r'merged_rack_door\s*\{[^}]*faceType\s+internal;', text)
    assert re.search(r'merged_rack_door\s*\{[^}]*faceZone\s+merged_rack_door;', text)

    # No merged_door_*U.stl bucket files
    assert "merged_door_" not in text.replace("merged_rack_door", ""), \
        "No per-bucket merged_door_*U.stl entries expected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py::test_snappy_dict_merged_wall_baffle_entry -v`
Expected: FAIL — current code generates individual `baffle_*` entries, not `merged_wall_baffle`.

- [ ] **Step 3: Update `_generate_snappy_hex_mesh_dict()` geometry, features, and refinementSurfaces sections**

In `src/backend/aircooling/runner/stlgen_runner.py`, modify the three sections inside `_generate_snappy_hex_mesh_dict()`:

**Geometry section** (~line 3789): Add handling for `merged_wall_baffle.stl` and `merged_rack_door.stl` — no regions block, same as existing baffle treatment:

```python
elif filename in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
    geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
    continue
```

**Features section** (~line 3866): Add eMesh entries for merged files:

```python
elif filename in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
    if filename == "merged_rack_door.stl":
        feat_level = _door_feature_level_for_rack({}, mc)
    else:
        feat_level = feat_baffle
    feature_lines.append(f"""    {{
        file "{stem}.eMesh";
        level {feat_level};
    }}""")
    continue
```

**refinementSurfaces section** (~line 4058): Replace individual baffle/door entries:

```python
elif filename == "merged_wall_baffle.stl":
    refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType baffle;
        faceZone {stem};
    }}""")
    continue

elif filename == "merged_rack_door.stl":
    door_rl = list(mc.get("rackDoorRefinementLevel") or [1, 1])
    refine_lines.append(f"""    {stem}
    {{
        level ({door_rl[0]} {door_rl[1]});
        faceType internal;
        faceZone {stem};
    }}""")
    continue
```

Also update handling for `perforated_baffle_*.stl` and `perforated_floor_*.stl`:

```python
elif filename.startswith("perforated_baffle_") or filename.startswith("perforated_floor_"):
    solid_san = _sanitize(solids[0])
    refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
    continue
```

Remove the old `filename.startswith("baffle_")` and `filename in rack_door_filenames` branches that handle individual files.

- [ ] **Step 4: Run tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_baffle_merge.py
git commit -m "feat(runner): snappyHexMeshDict uses merged wall baffle + rack door entries"
```

---

### Task 4: Update createBafflesDict and bridge BC for merged files

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (`_generate_create_baffles_dict`, `_build_bridge_bc`, `_convert_wall_baffle_patches_to_wall`)
- Test: `src/backend/aircooling/runner/tests/test_baffle_merge.py`

- [ ] **Step 1: Write failing test**

Add to `test_baffle_merge.py`:

```python
def test_create_baffles_dict_no_wall_entry(runner):
    """createBafflesDict should NOT have an entry for merged_wall_baffle
    (faceType baffle handles it directly)."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()

    cbd_path = BASE_LEAKAGE_DIR / "Mesh" / "system" / "createBafflesDict"
    text = cbd_path.read_text()

    assert "merged_wall_baffle" not in text, \
        "merged_wall_baffle should NOT appear in createBafflesDict"


def test_create_baffles_dict_has_door_entry(runner):
    """createBafflesDict should have ONE cyclic entry for merged_rack_door."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()

    cbd_path = BASE_LEAKAGE_DIR / "Mesh" / "system" / "createBafflesDict"
    text = cbd_path.read_text()

    import json
    with open(CFDRUN) as f:
        data = json.load(f)
    has_doors = any(
        r.get(f"{side}Door", {}).get("enabled", False)
        for r in data.get("racks", {}).values()
        for side in ("inlet", "outlet")
    )
    if not has_doors:
        pytest.skip("No doors in Base_leakage")

    assert "merged_rack_door" in text
    assert "merged_rack_door_master" in text
    assert "merged_rack_door_slave" in text
    assert re.search(r'type\s+cyclic;', text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py::test_create_baffles_dict_no_wall_entry -v`
Expected: FAIL.

- [ ] **Step 3: Update `_generate_create_baffles_dict()`**

Key changes in `_generate_create_baffles_dict()` (lines 4480–4660):

1. When iterating `self._stl_solids`, handle `merged_rack_door.stl` specially — create ONE cyclic entry with `zoneName merged_rack_door`.
2. Skip `merged_wall_baffle.stl` entirely — `faceType baffle` already created wall patches.
3. Handle `perforated_baffle_*.stl` and `perforated_floor_*.stl` like existing `baffle_*.stl` (individual cyclic entries).
4. Remove handling for individual `baffle_*.stl` door entries (they no longer exist).

For `merged_rack_door.stl`:
```python
if filename == "merged_rack_door.stl":
    # One shared faceZone → one cyclic pair for all doors
    sigma = self._get_uniform_door_sigma()
    d_coeff, f_coeff = _darcy_forchheimer(sigma, ...)
    baffle_configs.append({
        "zone_name": "merged_rack_door",
        "porosity": sigma,
        "d_coeff": d_coeff,
        "baffleType": "perforated",
    })
    continue
```

Add helper method `_get_uniform_door_sigma()`:
```python
def _get_uniform_door_sigma(self) -> float:
    """Return the shared sigma for all rack doors. Raises if inconsistent."""
    sigmas = set()
    for rack_name, rack in self._data.get("racks", {}).items():
        for side in ("inlet", "outlet"):
            door = rack.get(f"{side}Door")
            if not door or not door.get("enabled"):
                continue
            perf = door.get("perforated") or {"openAreaPct": 64.0}
            sigmas.add(float(perf.get("openAreaPct", 64.0)) / 100.0)
    if len(sigmas) > 1:
        raise ValueError(f"All rack doors must have same openAreaPct, got: {sigmas}")
    return sigmas.pop() if sigmas else 0.64
```

- [ ] **Step 4: Update `_build_bridge_bc()` for merged patch names**

In `_build_bridge_bc()`, the rack door registration section (~lines 3215–3247) currently registers individual door solid names. Change to register the merged patch names:

```python
# Instead of per-door registration, register merged pair
if has_any_door:
    sigma = self._get_uniform_door_sigma()
    perf_spec = PerforatedSpec(openAreaPct=sigma * 100.0, ...)
    registry.register("merged_rack_door_master", BCSpec(
        bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
    registry.register("merged_rack_door_slave", BCSpec(
        bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
```

For `merged_wall_baffle`, register the snappy-generated patch names:
```python
# faceType baffle creates patches named: merged_wall_baffle_<firstSolid> + _slave
first_solid = self._stl_solids.get("merged_wall_baffle.stl", ["unknown"])[0]
first_san = _sanitize(first_solid)
registry.register(f"merged_wall_baffle_{first_san}", BCSpec(bc_type="wall_baffle"))
registry.register(f"merged_wall_baffle_{first_san}_slave", BCSpec(bc_type="wall_baffle"))
```

- [ ] **Step 5: Run tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_baffle_merge.py
git commit -m "feat(runner): createBafflesDict + bridge BC for merged baffles/doors"
```

---

### Task 5: Update dual-side seed generation for merged files

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (`_collect_create_baffle_seed_specs`, `_build_patch_driven_none_seed_entries`)
- Test: `src/backend/aircooling/runner/tests/test_baffle_merge.py`

- [ ] **Step 1: Write failing test**

Add to `test_baffle_merge.py`:

```python
def test_seeds_cover_all_baffle_locations(runner):
    """Dual-side seeds must exist for every wall baffle and door location,
    even though they're merged into shared files."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()

    snappy_path = BASE_LEAKAGE_DIR / "Mesh" / "system" / "snappyHexMeshDict"
    text = snappy_path.read_text()

    # Count locationsInMesh entries with "none" zone
    none_seeds = re.findall(r'\(\s*[\d.e+-]+\s+[\d.e+-]+\s+[\d.e+-]+\s*\)\s*\{\s*name\s+none;', text)

    # Base_leakage has: 3 wall baffles + 4 wallBaffle rack solids + 1 floor wallBaffle
    # + 2 doors + 1 perforated floor = 11 baffles × 2 sides = 22 seeds
    # Plus any partition hole seeds
    assert len(none_seeds) >= 20, \
        f"Expected ≥20 dual-side seeds, got {len(none_seeds)}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py::test_seeds_cover_all_baffle_locations -v`

- [ ] **Step 3: Update `_collect_create_baffle_seed_specs()`**

The seed collection must iterate over all solids in `merged_wall_baffle.stl` and `merged_rack_door.stl`, generating a seed spec for each solid (not just one per file). The existing logic works per-file — update it to iterate the merged file's solid list.

Key: for `merged_wall_baffle.stl`, each solid in `self._stl_solids["merged_wall_baffle.stl"]` needs its own seed pair. The `patch_kind` should be `"baffle_model_wall"` for wall baffles and `"baffle_model_perforated"` for doors.

For `merged_rack_door.stl`, each solid gets `patch_kind="baffle_model_perforated"`.

For individual `perforated_baffle_*.stl` and `perforated_floor_*.stl`, the existing per-file logic already works.

- [ ] **Step 4: Run tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_baffle_merge.py
git commit -m "feat(runner): dual-side seeds iterate all solids in merged baffle files"
```

---

### Task 6: Frontend — merge door export + porosity validation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`buildGroupedStlMap`, `runMeshGeneration`)

- [ ] **Step 1: Replace per-bucket door export with single merged file**

In `buildGroupedStlMap()` (~lines 19005–19022), replace the `doorParts` per-bucket logic:

```javascript
// BEFORE: per-bucket merged_door_<N>U.stl
// AFTER: single merged_rack_door.stl for ALL doors
const allDoorParts = [];
state.models.forEach(function(model) {
    if (!idSet.has(model.id) || model.kind !== 'rack') return;
    if (!isModelEnabled(model) && !disabledExtras.includes(model)) return;
    var rack = model.rack;
    ['inlet', 'outlet'].forEach(function(side) {
        var door = rack[side + 'Door'];
        if (!door || !door.enabled) return;
        var doorStl = _buildSingleDoorStl(model, side);
        if (doorStl) allDoorParts.push(doorStl);
    });
});
if (allDoorParts.length > 0) {
    result['merged_rack_door.stl'] = allDoorParts.join('');
}
```

Remove the old per-bucket `doorParts` code and `_computeRackMinUSpan` usage in this section.

- [ ] **Step 2: Add door porosity validation in `runMeshGeneration()`**

In `runMeshGeneration()` (~line 28310), add before Step 3 (STL export):

```javascript
// Door porosity uniformity check
if (mode !== 'solve') {
    var doorPorosities = new Map();
    state.models.forEach(function(m) {
        if (m.kind !== 'rack') return;
        ['inlet', 'outlet'].forEach(function(side) {
            var door = m.rack[side + 'Door'];
            if (!door || !door.enabled) return;
            var pct = (door.perforated && door.perforated.openAreaPct != null)
                ? door.perforated.openAreaPct : 64;
            doorPorosities.set(sanitizeName(m.name) + '_' + side, pct);
        });
    });
    var uniquePcts = new Set(doorPorosities.values());
    if (uniquePcts.size > 1) {
        var details = Array.from(doorPorosities.entries())
            .map(([k, v]) => k + ': ' + v + '%').join('\n');
        alert('所有 Rack 穿孔门必须使用相同的 openAreaPct。\n当前设置:\n' + details);
        if (statusEl) statusEl.textContent = '';
        if (runBtn) runBtn.disabled = false;
        return;
    }
}
```

- [ ] **Step 3: Test manually with browser-use or by inspection**

Load Base_leakage in browser, trigger mesh generation, verify:
1. `merged_rack_door.stl` is uploaded (not `merged_door_*U.stl`)
2. No porosity validation error (Base_leakage has uniform door settings)

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): merge all doors into merged_rack_door.stl + porosity validation"
```

---

### Task 7: Frontend — simplify door refinement UI in mesh tab

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (door refinement section in mesh tab, `_autoDetectRackDoor`, `scanRackDoorMinUnitBuckets`)

- [ ] **Step 1: Simplify `_autoDetectRackDoor()`**

Replace the per-bucket door auto-detect (~lines 26138–26224) with a simplified version that computes one unified level:

```javascript
function _autoDetectRackDoor() {
    var baseSize = _getDetectBaseSize();
    var changes = [];
    // Find minimum door patch height across all racks
    var minPatchH = Infinity;
    state.models.forEach(function(m) {
        if (m.kind !== 'rack') return;
        ['inlet', 'outlet'].forEach(function(side) {
            var door = m.rack[side + 'Door'];
            if (!door || !door.enabled) return;
            var centralH = m.rack.unitCount * m.rack.unitHeight;
            if (centralH > 0 && centralH < minPatchH) minPatchH = centralH;
        });
    });
    if (minPatchH === Infinity) return changes;

    var safeLevel = computeMinSafeLevel(baseSize, minPatchH);
    var cur = state.cfdRoom.rackDoorRefinementLevel || [1, 1];
    if (cur[0] !== safeLevel[0] || cur[1] !== safeLevel[1]) {
        changes.push('door: [' + cur.join(',') + '] → [' + safeLevel.join(',') + ']');
        state.cfdRoom.rackDoorRefinementLevel = safeLevel;
    }
    // Feature level = surface max
    var curFeat = state.cfdRoom.rackDoorFeatureLevel;
    if (curFeat == null || curFeat !== safeLevel[1]) {
        state.cfdRoom.rackDoorFeatureLevel = safeLevel[1];
    }
    return changes;
}
```

- [ ] **Step 2: Update door refinement UI rendering**

Find the `_renderCategorySection` call for doors in the mesh tab UI. Replace the per-bucket row rendering with a simple unified control showing 3 inputs:
- Surface level min
- Surface level max  
- Feature level

Remove per-bucket UI rows for doors.

- [ ] **Step 3: Remove `scanRackDoorMinUnitBuckets()` if no longer referenced**

Check if `scanRackDoorMinUnitBuckets()` is used anywhere else. If only used by the old per-bucket door code, remove it.

- [ ] **Step 4: Test manually**

Open mesh tab, verify door refinement shows as 3 simple inputs (not per-bucket rows).

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(frontend): simplify door refinement to unified level (no per-bucket)"
```

---

### Task 8: Integration test — Base_leakage full pipeline

**Files:**
- Test: `src/backend/aircooling/runner/tests/test_baffle_merge.py`

- [ ] **Step 1: Write integration test**

Add to `test_baffle_merge.py`:

```python
def test_base_leakage_full_mesh_pipeline(runner):
    """Run complete mesh pipeline on Base_leakage and verify results."""
    import subprocess
    mesh_dir = BASE_LEAKAGE_DIR / "Mesh"

    # Clean
    poly = mesh_dir / "constant" / "polyMesh"
    if poly.exists():
        import shutil
        shutil.rmtree(poly)

    # Run full mesh
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()

    # Verify snappyHexMeshDict size reduced
    snappy = mesh_dir / "system" / "snappyHexMeshDict"
    line_count = len(snappy.read_text().splitlines())
    assert line_count < 2000, \
        f"snappyHexMeshDict should be < 2000 lines, got {line_count}"

    # Verify file counts
    stl_files = list((mesh_dir / "constant" / "triSurface").glob("*.stl"))
    baffle_files = [f for f in stl_files if "baffle" in f.name or "door" in f.name or "perforated" in f.name]
    assert len(baffle_files) <= 6, \
        f"Expected ≤ 6 baffle/door/perforated files, got {len(baffle_files)}: {[f.name for f in baffle_files]}"
```

- [ ] **Step 2: Run test**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_baffle_merge.py::test_base_leakage_full_mesh_pipeline -v`
Expected: PASS.

- [ ] **Step 3: Run full test suite for regressions**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v --tb=short`
Expected: ALL PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add src/backend/aircooling/runner/tests/test_baffle_merge.py
git commit -m "test(runner): integration test for baffle merge on Base_leakage"
```
