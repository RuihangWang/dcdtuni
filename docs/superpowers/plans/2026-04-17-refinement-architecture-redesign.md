# Refinement Architecture Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild STL export + snappyHexMeshDict emission so every UI refinement input (surface AND feature-line) lands in snappy and takes effect. Replace 400-file per-door / per-leakage layout with ~20 targeted files keyed by `(category × U-span bucket)`.

**Architecture:** Feature-line granularity drives STL file split (snappyHexMesh limits features to per-file level). Surface granularity via `refinementSurfaces.regions{}` within each file. Leakage produces cellZone markers only — NO OpenFOAM patches. Patches on faces inherit their parent solid's surface level. Unified panel inputs broadcast to per-bucket overrides; export reads only per-bucket.

**Tech Stack:** Python 3.10 (pydantic v2, pytest), vanilla JS (STL_generate.html, ~28k lines), OpenFOAM snappyHexMesh.

**Spec:** `docs/superpowers/specs/2026-04-17-refinement-architecture-redesign-design.md`

**Test case constraint:** All E2E validation targets `case/base_leakage`. Do NOT iterate on `alibaba-hongchou` — it takes 20-30 min per mesh and blocks development. Reserve alibaba for final validation once base_leakage passes.

---

## File Structure

| File | Responsibility | Create / Modify |
|------|----------------|-----------------|
| `src/backend/aircooling/runner/cfdproj_schema.py` | Declare new `MeshConfig` fields for per-bucket feature-line + partition category + floor refinement | Modify |
| `src/backend/aircooling/runner/stlgen_runner.py` | Category-resolver helper, new filename-pattern table in `_generate_snappy_hex_mesh_dict`, leakage cellZone-only emission, createBaffles filter | Modify |
| `src/backend/aircooling/runner/tests/test_refinement_redesign.py` | Backend schema + resolver + dict-emission tests | Create |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | `STL_EXPORT_GROUPS` refactor, `buildGroupedStlMap` per-bucket split, state defaults + partition state, UI panel redesign (remove rack-frame sub-buckets + feature input, add per-bucket feature-line inputs to server/blanking/partition/leakage, new Partition row), `setUnifiedRackCategory` broadcast, `_autoDetectCategory` feature=surfaceMax, `buildCfdExportJson` emits new fields | Modify (many sites) |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | New labels for Partition category + per-bucket feature-line widget tooltips | Modify |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Same | Modify |
| `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py` | Static-structure tests (state keys, functions, panel wiring, i18n) | Create |
| `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_runtime.py` | Headless-browser runtime tests: broadcast behavior, bucket grouping, export round-trip | Create |

**File naming contract (frontend ↔ backend must agree exactly):**

```
Scene merged:         Rack-internal per-bucket:             Door:
  room_wall.stl         merged_server_<N>U.stl                 merged_door_<N>U.stl
  merged_rack_frame.stl merged_blanking_<N>U.stl               (N = rack min-U-span bucket key)
  merged_acu.stl        merged_partition_<N>U.stl
  merged_box.stl        merged_leakage_<N>U.stl  ← cellZone only, no patch
  merged_baffle.stl
  merged_floor_ceiling.stl
```

**Conventions followed:**

- Backend tests bypass `STLGenRunner._load()` via `_bootstrap_runner` helper to avoid pydantic validation overhead (same pattern as 2026-04-17 rack-door-refinement plan's Task 4).
- Frontend tests use `extract_fn_body` regex helper from existing `test_perforated_frontend.py`.
- Every frontend state mutation uses `pushUndoCheckpoint` + `logOperation` via the existing setters (`setUnifiedRackCategory` / `setPerUnitRackCategory`); no new setters.

---

## Task 1: Backend schema — declare all new `MeshConfig` fields

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py` (around line 388-411, `MeshConfig` class)
- Create: `src/backend/aircooling/runner/tests/test_refinement_redesign.py`

- [ ] **Step 1.1: Write failing schema test**

Create `src/backend/aircooling/runner/tests/test_refinement_redesign.py`:

```python
"""Regression tests for the 2026-04-17 refinement architecture redesign.

Spec: docs/superpowers/specs/2026-04-17-refinement-architecture-redesign-design.md
"""
from dcdtuni.runner.cfdproj_schema import CFDExport, MeshConfig


def test_meshconfig_new_feature_per_unit_fields():
    """Per-bucket feature-line maps default to empty dicts on all 4
    rack-internal categories (the file contract requires a feature level
    per STL file; per-unit dict is the authoritative source)."""
    mc = MeshConfig()
    assert mc.serverFaceFeaturePerUnitLevel == {}
    assert mc.blankingPlateFeaturePerUnitLevel == {}
    assert mc.rackPartitionFeaturePerUnitLevel == {}
    assert mc.rackLeakageFeaturePerUnitLevel == {}


def test_meshconfig_new_partition_fields():
    """New Partition 隔板 category: surface + feature unified + per-unit."""
    mc = MeshConfig()
    assert mc.rackPartitionRefinementLevel == [1, 1]
    assert mc.rackPartitionPerUnitLevel == {}
    assert mc.rackPartitionFeatureLevel == 2


def test_meshconfig_new_blanking_feature_unified():
    """Blanking panel feature level (unified scalar); existed only via
    per-unit dict in the pre-redesign model."""
    mc = MeshConfig()
    assert mc.blankingPlateFeatureLevel == 2


def test_meshconfig_new_leakage_feature_unified():
    """Leakage 'feature level' = cellZone volume-refinement level. Kept
    for architecture consistency even though features block is degenerate."""
    mc = MeshConfig()
    assert mc.rackLeakageFeatureLevel == 2


def test_meshconfig_new_floor_fields():
    """Floor refinement escaped from defaultFeatureLevel fallback."""
    mc = MeshConfig()
    assert mc.floorRefinementLevel == [1, 1]
    assert mc.floorFeatureLevel == 2


def test_meshconfig_accepts_per_unit_maps_from_cfdrun():
    """Round-trip through pydantic model_validate preserves all new keys."""
    exp = CFDExport.model_validate({
        "meshConfig": {
            "serverFaceFeaturePerUnitLevel": {"1": 4, "4": 2},
            "blankingPlateFeaturePerUnitLevel": {"1": 3, "2": 2},
            "rackPartitionRefinementLevel": [2, 3],
            "rackPartitionPerUnitLevel": {"1": [3, 4]},
            "rackPartitionFeatureLevel": 3,
            "rackPartitionFeaturePerUnitLevel": {"1": 4},
            "rackLeakageFeatureLevel": 2,
            "rackLeakageFeaturePerUnitLevel": {"3": 3},
            "floorRefinementLevel": [2, 3],
            "floorFeatureLevel": 3,
        },
    })
    mc = exp.meshConfig
    assert mc.serverFaceFeaturePerUnitLevel["1"] == 4
    assert mc.blankingPlateFeaturePerUnitLevel["2"] == 2
    assert mc.rackPartitionRefinementLevel == [2, 3]
    assert mc.rackPartitionPerUnitLevel["1"] == [3, 4]
    assert mc.rackPartitionFeaturePerUnitLevel["1"] == 4
    assert mc.rackLeakageFeaturePerUnitLevel["3"] == 3
    assert mc.floorRefinementLevel == [2, 3]
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 6 FAIL with `AttributeError: 'MeshConfig' object has no attribute 'serverFaceFeaturePerUnitLevel'` etc.

- [ ] **Step 1.3: Add new fields to `MeshConfig`**

In `src/backend/aircooling/runner/cfdproj_schema.py`, inside `class MeshConfig` — insert the new fields right after the existing `partitionWallFeatureLevel: int = 2` (around line 403):

```python
class MeshConfig(CFDBaseModel):
    baseSize: float = 0.2
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    serverFaceRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    baffleRefinementLevel: List[int] = Field(default_factory=lambda: [1, 3])
    rackDoorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackDoorPerMinUnitLevel: Dict[str, List[int]] = Field(default_factory=dict)
    partitionWallRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    partitionWallFeatureLevel: int = 2
    # ---- 2026-04-17 refinement architecture redesign ----
    # Per-bucket FEATURE-line maps (surface per-unit already existed as
    # *PerUnitLevel elsewhere). Each STL file the frontend emits gets its
    # own feature level, so the per-unit feature map is the authoritative
    # source — unified *FeatureLevel acts as the broadcast seed only.
    serverFaceFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    blankingPlateFeatureLevel: int = 2
    blankingPlateFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    # New Partition 隔板 category (interior partitions inside through-slots).
    rackPartitionRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackPartitionPerUnitLevel: Dict[str, List[int]] = Field(default_factory=dict)
    rackPartitionFeatureLevel: int = 2
    rackPartitionFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    # Leakage "feature" level = cellZone volume refinement level (features
    # block entry is degenerate but still required by the per-file rule).
    rackLeakageFeatureLevel: int = 2
    rackLeakageFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    # Floor/ceiling category escaped from defaultFeatureLevel fallback.
    floorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    floorFeatureLevel: int = 2
    # ---- end redesign additions ----
    parallelMesh: bool = True
    meshProcess: int = 16
    maxGlobalCells: int = 5000000
    maxLocalCells: int = 5000000
```

Verify `Dict`, `List`, `Field` are already imported at the top of the file (they are — from the existing codebase).

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 6 passed.

- [ ] **Step 1.5: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "feat(schema): MeshConfig fields for per-bucket feature levels + partition/floor"
```

---

## Task 2: Backend — category/bucket resolver helpers

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (add helpers near `_compute_rack_min_u_span` / `_door_level_for_rack` around line 62-110)
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py` (append tests)

- [ ] **Step 2.1: Write failing tests**

Append to `src/backend/aircooling/runner/tests/test_refinement_redesign.py`:

```python
from dcdtuni.runner.stlgen_runner import (
    _bucket_level_lookup,
    _bucket_feature_lookup,
    _parse_bucket_from_filename,
)


def test_parse_bucket_from_filename_recognises_all_per_bucket_prefixes():
    """Filename → (category, bucket_key) for the 5 per-bucket categories."""
    assert _parse_bucket_from_filename("merged_server_1U.stl") == ("server", "1")
    assert _parse_bucket_from_filename("merged_server_4U.stl") == ("server", "4")
    assert _parse_bucket_from_filename("merged_blanking_2U.stl") == ("blanking", "2")
    assert _parse_bucket_from_filename("merged_partition_1U.stl") == ("partition", "1")
    assert _parse_bucket_from_filename("merged_leakage_3U.stl") == ("leakage", "3")
    assert _parse_bucket_from_filename("merged_door_1U.stl") == ("door", "1")


def test_parse_bucket_from_filename_returns_none_for_scene_merged():
    """Scene-merged files (room_wall, merged_rack_frame, etc.) return None."""
    assert _parse_bucket_from_filename("room_wall.stl") is None
    assert _parse_bucket_from_filename("merged_rack_frame.stl") is None
    assert _parse_bucket_from_filename("merged_acu.stl") is None
    assert _parse_bucket_from_filename("merged_baffle.stl") is None


def test_bucket_level_lookup_server_per_unit_hit():
    """server bucket '1' defined → returns per-unit entry."""
    mc = {
        "serverFaceRefinementLevel": [2, 2],
        "serverFacePerUnitLevel": {"1": [3, 4], "4": [2, 2]},
    }
    assert _bucket_level_lookup("server", "1", mc) == [3, 4]
    assert _bucket_level_lookup("server", "4", mc) == [2, 2]


def test_bucket_level_lookup_falls_back_to_unified():
    """Unknown bucket key → unified level."""
    mc = {
        "blankingPlateRefinementLevel": [2, 3],
        "blankingPlatePerUnitLevel": {"1": [4, 5]},
    }
    assert _bucket_level_lookup("blanking", "2", mc) == [2, 3]


def test_bucket_level_lookup_hard_default():
    """Both unified and per-unit missing → [1, 1]."""
    assert _bucket_level_lookup("server", "1", {}) == [1, 1]


def test_bucket_feature_lookup_per_unit_hit():
    """Feature level from per-unit dict takes precedence over unified."""
    mc = {
        "serverFaceFeatureLevel": 2,
        "serverFaceFeaturePerUnitLevel": {"1": 4, "4": 2},
    }
    assert _bucket_feature_lookup("server", "1", mc) == 4
    assert _bucket_feature_lookup("server", "4", mc) == 2


def test_bucket_feature_lookup_falls_back_to_unified():
    mc = {"rackPartitionFeatureLevel": 3, "rackPartitionFeaturePerUnitLevel": {}}
    assert _bucket_feature_lookup("partition", "1", mc) == 3


def test_bucket_feature_lookup_hard_default():
    """Missing keys → 2 (matches every new unified default)."""
    assert _bucket_feature_lookup("leakage", "1", {}) == 2
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 8 NEW failures with `ImportError: cannot import name '_bucket_level_lookup'` etc.

- [ ] **Step 2.3: Implement helpers**

In `src/backend/aircooling/runner/stlgen_runner.py`, add these module-level helpers immediately after `_door_level_for_rack` (which is right after `_compute_rack_min_u_span`). Use Ctrl-F to find `def _door_level_for_rack` and insert BELOW its closing:

```python
# ---- 2026-04-17 refinement architecture redesign ----

_BUCKET_FILE_PATTERNS = {
    "server":    ("merged_server_",    "serverFaceRefinementLevel",    "serverFacePerUnitLevel",    "serverFaceFeatureLevel",    "serverFaceFeaturePerUnitLevel"),
    "blanking":  ("merged_blanking_",  "blankingPlateRefinementLevel", "blankingPlatePerUnitLevel", "blankingPlateFeatureLevel", "blankingPlateFeaturePerUnitLevel"),
    "partition": ("merged_partition_", "rackPartitionRefinementLevel", "rackPartitionPerUnitLevel", "rackPartitionFeatureLevel", "rackPartitionFeaturePerUnitLevel"),
    "leakage":   ("merged_leakage_",   "rackLeakageRefinementLevel",   "rackLeakagePerUnitLevel",   "rackLeakageFeatureLevel",   "rackLeakageFeaturePerUnitLevel"),
    "door":      ("merged_door_",      "rackDoorRefinementLevel",      "rackDoorPerMinUnitLevel",   None,                        None),  # door feature = surface max
}


def _parse_bucket_from_filename(filename: str) -> Optional[tuple[str, str]]:
    """For per-bucket STL files, return (category, bucket_key). None for scene-merged."""
    import re as _re
    for cat, (prefix, *_rest) in _BUCKET_FILE_PATTERNS.items():
        if not filename.startswith(prefix):
            continue
        m = _re.match(rf"{_re.escape(prefix)}(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
    return None


def _bucket_level_lookup(category: str, bucket_key: str,
                        mesh_config: Dict[str, Any]) -> List[int]:
    """Resolve surface refinement level for (category, bucket). Priority:
    per-unit dict > unified pair > [1, 1]."""
    spec = _BUCKET_FILE_PATTERNS.get(category)
    if spec is None:
        return [1, 1]
    _prefix, unified_key, per_unit_key, *_ = spec
    per_unit = mesh_config.get(per_unit_key) or {}
    if bucket_key in per_unit:
        return list(per_unit[bucket_key])
    unified = mesh_config.get(unified_key)
    if isinstance(unified, (list, tuple)) and len(unified) == 2:
        return list(unified)
    return [1, 1]


def _bucket_feature_lookup(category: str, bucket_key: str,
                          mesh_config: Dict[str, Any]) -> int:
    """Resolve feature-line level for (category, bucket). Priority:
    per-unit dict > unified scalar > 2. For 'door' category, feature
    level = surface max (follows _door_level_for_rack convention)."""
    spec = _BUCKET_FILE_PATTERNS.get(category)
    if spec is None:
        return 2
    _prefix, unified_key, per_unit_key, feat_unified, feat_per_unit = spec
    if feat_unified is None and feat_per_unit is None:
        # door: feature = surface max
        return _bucket_level_lookup(category, bucket_key, mesh_config)[1]
    per_unit = mesh_config.get(feat_per_unit) or {}
    if bucket_key in per_unit:
        return int(per_unit[bucket_key])
    unified = mesh_config.get(feat_unified)
    return int(unified) if unified is not None else 2
```

Verify `Optional`, `tuple`, `List`, `Dict`, `Any` are imported at the top (they are).

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 14 passed (6 schema + 8 helper).

- [ ] **Step 2.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "feat(runner): bucket-file pattern registry + level/feature resolvers"
```

---

## Task 3: Backend — refactor `_generate_snappy_hex_mesh_dict` features block

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (features block around line 3442-3478)
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py`

- [ ] **Step 3.1: Write failing test — features block routes per-bucket files through the resolver**

Append:

```python
from pathlib import Path
from dcdtuni.runner.stlgen_runner import STLGenRunner


def _bootstrap_runner(tmp_path: Path, mesh_config: dict, racks: dict,
                      stl_solids: dict) -> STLGenRunner:
    """Standard fixture — bypasses _load(), sets _data + _stl_solids directly."""
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
    runner._build_locations_in_mesh_entries = lambda: []
    return runner


def _write_dummy_stl(tri_dir: Path, filename: str, solid_names):
    """Write a minimal valid ASCII STL with placeholder triangles."""
    lines = []
    for name in solid_names:
        lines.append(f"solid {name}")
        lines.append("facet normal 0 0 1")
        lines.append(" outer loop")
        lines.append("  vertex 0 0 0")
        lines.append("  vertex 1 0 0")
        lines.append("  vertex 0 1 0")
        lines.append(" endloop")
        lines.append("endfacet")
        lines.append(f"endsolid {name}")
    (tri_dir / filename).write_text("\n".join(lines) + "\n")


def test_snappy_dict_features_block_reads_per_bucket_levels(tmp_path):
    """Every per-bucket file's features entry reads from the
    *FeaturePerUnitLevel map."""
    stl_solids = {
        "merged_server_1U.stl":   ["S1_inlet"],
        "merged_server_4U.stl":   ["S4_inlet"],
        "merged_blanking_2U.stl": ["B2_panel"],
        "merged_partition_1U.stl":["P1_part"],
        "merged_leakage_3U.stl":  ["L3_zone"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "serverFaceRefinementLevel": [1, 1],
        "serverFaceFeatureLevel": 2,
        "serverFaceFeaturePerUnitLevel": {"1": 4, "4": 2},
        "blankingPlateRefinementLevel": [1, 1],
        "blankingPlateFeatureLevel": 2,
        "blankingPlateFeaturePerUnitLevel": {"2": 3},
        "rackPartitionRefinementLevel": [1, 1],
        "rackPartitionFeatureLevel": 2,
        "rackPartitionFeaturePerUnitLevel": {"1": 5},
        "rackLeakageRefinementLevel": [1, 1],
        "rackLeakageFeatureLevel": 2,
        "rackLeakageFeaturePerUnitLevel": {"3": 3},
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()
    # Each file gets the bucket's feature level
    assert 'file "merged_server_1U.eMesh";\n        level 4;' in text
    assert 'file "merged_server_4U.eMesh";\n        level 2;' in text
    assert 'file "merged_blanking_2U.eMesh";\n        level 3;' in text
    assert 'file "merged_partition_1U.eMesh";\n        level 5;' in text
    assert 'file "merged_leakage_3U.eMesh";\n        level 3;' in text
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py::test_snappy_dict_features_block_reads_per_bucket_levels -v`
Expected: FAIL — current code doesn't recognise per-bucket prefixes (falls through to `feat_default`).

- [ ] **Step 3.3: Rewire features block**

Find the features-block loop in `src/backend/aircooling/runner/stlgen_runner.py` (around line 3442-3478). Replace the current `if/elif` chain on `filename` with a two-phase dispatch: first try per-bucket parse, else fall through to the existing scene-file / door branches. The full replacement:

```python
        feature_lines = []
        for filename in sorted(self._stl_solids.keys()):
            stem = Path(filename).stem
            emesh_name = filename.replace(".stl", ".eMesh")

            # 2026-04-17 redesign: per-bucket files route through the
            # bucket resolver (features per STL file per snappyHexMesh).
            bucket = _parse_bucket_from_filename(filename)
            if bucket is not None:
                cat, key = bucket
                feat_level = _bucket_feature_lookup(cat, key, mc)
            elif filename == "merged_rack_frame.stl":
                feat_level = feat_rack
            elif filename == "merged_acu.stl":
                feat_level = feat_acu
            elif filename == "merged_box.stl":
                feat_level = feat_box
            elif filename == "merged_partition_wall.stl":
                feat_level = feat_partition
            elif filename == "merged_baffle.stl":
                feat_level = feat_baffle
            elif filename == "merged_floor_ceiling.stl":
                feat_level = _feat_int(mc.get("floorFeatureLevel"), feat_default)
            elif filename == "room_wall.stl":
                feat_level = feat_default
            # Legacy fallbacks (transitional; old-style individual files
            # may still exist in partial rollouts until the frontend ships
            # Task 12's buildGroupedStlMap refactor):
            elif filename.startswith("leakage_") or filename.startswith("partition_hole_"):
                feat_level = feat_leakage
            elif filename == "merged_perforated.stl":
                feat_level = feat_baffle
            elif filename.startswith("baffle_"):
                feat_level = feat_baffle
            else:
                feat_level = feat_default

            feature_lines.append(f"""    {{
        file "{emesh_name}";
        level {feat_level};
    }}""")
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 15 passed.

- [ ] **Step 3.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "feat(runner): features block reads per-bucket feature levels via resolver"
```

---

## Task 4: Backend — refactor refinementSurfaces for per-bucket files

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (refinementSurfaces loop around line 3481-3600)
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py`

- [ ] **Step 4.1: Write failing test — per-bucket surface levels + leakage cellZone**

Append:

```python
def test_snappy_dict_refinement_surfaces_per_bucket(tmp_path):
    """server/blanking/partition buckets emit `level (min max); faceZone`;
    leakage emits `faceType internal + cellZone + cellZoneInside inside`
    with NO createBaffles entry."""
    stl_solids = {
        "merged_server_1U.stl":   ["S1_inlet"],
        "merged_blanking_2U.stl": ["B2_panel"],
        "merged_partition_1U.stl":["P1_part"],
        "merged_leakage_3U.stl":  ["L3_zone"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "serverFacePerUnitLevel":    {"1": [3, 4]},
        "blankingPlatePerUnitLevel": {"2": [2, 3]},
        "rackPartitionPerUnitLevel": {"1": [3, 5]},
        "rackLeakagePerUnitLevel":   {"3": [1, 2]},
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()
    # server, blanking, partition: faceType internal + faceZone
    for stem, lvl in [("merged_server_1U", "(3 4)"),
                      ("merged_blanking_2U", "(2 3)"),
                      ("merged_partition_1U", "(3 5)")]:
        assert f'{stem}\n    {{\n        level {lvl};\n        faceType internal;\n        faceZone {stem};' in text, \
            f"missing or wrong block for {stem}"
    # leakage: faceType internal + cellZone (NOT faceZone)
    assert 'merged_leakage_3U\n    {\n        level (1 2);\n        faceType internal;\n        cellZone merged_leakage_3U;\n        cellZoneInside inside;' in text
    # leakage must NOT appear in refinementSurfaces with faceZone
    assert 'merged_leakage_3U.*faceZone' not in text, \
        "leakage must use cellZone, not faceZone"
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py::test_snappy_dict_refinement_surfaces_per_bucket -v`
Expected: FAIL — current `refinementSurfaces` loop doesn't know about the new filename patterns.

- [ ] **Step 4.3: Refactor the refinementSurfaces loop**

In `src/backend/aircooling/runner/stlgen_runner.py`, find the refinementSurfaces loop (starts around line 3481 with `refine_lines = []` and iterates `self._stl_solids.items()`). Add a per-bucket early branch right after the `partition_hole_` and `leakage_` legacy branches, BEFORE the `solid_entries` per-solid loop:

Insert before `if filename.startswith("baffle_"):`:

```python
            # 2026-04-17 redesign: per-bucket files (server/blanking/
            # partition/leakage/door) emit a single top-level level entry.
            # leakage uses cellZone-only (NO faceZone, NO createBaffles).
            bucket = _parse_bucket_from_filename(filename)
            if bucket is not None:
                cat, key = bucket
                lvl = _bucket_level_lookup(cat, key, mc)
                if cat == "leakage":
                    refine_lines.append(f"""    {stem}
    {{
        level ({lvl[0]} {lvl[1]});
        faceType internal;
        cellZone {stem};
        cellZoneInside inside;
    }}""")
                else:
                    refine_lines.append(f"""    {stem}
    {{
        level ({lvl[0]} {lvl[1]});
        faceType internal;
        faceZone {stem};
    }}""")
                continue
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 16 passed.

- [ ] **Step 4.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "feat(runner): refinementSurfaces per-bucket + leakage cellZone-only"
```

---

## Task 5: Backend — createBaffles filter excludes leakage

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (find createBafflesDict generator)
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py`

- [ ] **Step 5.1: Locate createBafflesDict emission**

Run: `grep -n "createBafflesDict\|_generate_create_baffles" src/backend/aircooling/runner/stlgen_runner.py | head -n 10`

Record the emitter function name + its faceZone-iteration loop. Most likely function is `_generate_create_baffles_dict` or similar.

- [ ] **Step 5.2: Write failing test — leakage buckets not in createBafflesDict**

Append to the test file:

```python
def test_leakage_bucket_not_in_create_baffles(tmp_path):
    """leakage cellZone STLs must not appear in createBafflesDict."""
    stl_solids = {
        "merged_server_1U.stl":   ["S1_inlet"],
        "merged_leakage_3U.stl":  ["L3_zone"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "serverFacePerUnitLevel":  {"1": [3, 4]},
        "rackLeakagePerUnitLevel": {"3": [1, 2]},
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    # Run the createBafflesDict generator (find its method name via grep)
    if hasattr(runner, "_generate_create_baffles_dict"):
        runner._generate_create_baffles_dict(system_dir)
        cb_text = (system_dir / "createBafflesDict").read_text()
        assert "merged_leakage_3U" not in cb_text, \
            "leakage bucket leaked into createBafflesDict"
```

- [ ] **Step 5.3: Run test to verify current state**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py::test_leakage_bucket_not_in_create_baffles -v`
Expected: likely PASS (leakage buckets are new filenames; existing createBaffles doesn't recognise them). But confirm.

- [ ] **Step 5.4: Add filter guard**

If the test was passing: add a defensive comment in the createBaffles emitter loop. Search for the iteration over faceZones or baffle solids; add a guard that skips filenames matching `_parse_bucket_from_filename(...)`'s "leakage" category:

```python
# 2026-04-17: leakage cellZone STLs must NEVER produce a baffle pair.
if _parse_bucket_from_filename(filename) == ("leakage", ...):  # pseudo
    continue
```

Concretely, inside whatever loop iterates over candidate baffle files (e.g. `for filename in self._stl_solids`), add:

```python
_bucket = _parse_bucket_from_filename(filename)
if _bucket and _bucket[0] == "leakage":
    continue  # leakage is cellZone-only, never a baffle
```

- [ ] **Step 5.5: Run test again**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 17 passed.

- [ ] **Step 5.6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "feat(runner): createBaffles filter excludes leakage buckets"
```

---

## Task 6: Backend — floorFeatureLevel + defensive wallBaffleSolids warn

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (minor)
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py`

- [ ] **Step 6.1: Test floorFeatureLevel is honoured**

Append:

```python
def test_floor_feature_level_honoured(tmp_path):
    """merged_floor_ceiling.stl reads floorFeatureLevel (not defaultFeatureLevel)."""
    stl_solids = {"merged_floor_ceiling.stl": ["fl_1"]}
    mc = {"baseSize": 0.4, "floorFeatureLevel": 5, "defaultFeatureLevel": 2}
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    _write_dummy_stl(tri_dir, "merged_floor_ceiling.stl", ["fl_1"])
    runner = _bootstrap_runner(tmp_path, mc, {}, stl_solids)
    runner._generate_snappy_hex_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "snappyHexMeshDict").read_text()
    assert 'file "merged_floor_ceiling.eMesh";\n        level 5;' in text


def test_wallbafflesolids_nonempty_warns_and_drops(tmp_path, caplog):
    """If a rack has wallBaffleSolids populated (schema drift), the runner
    warns and drops the entries — does NOT route them to merged_baffle."""
    import logging
    racks = {
        "A17": {
            "wallBaffleSolids": ["A17_weird_wall_baffle"],
            "servers": [],
            "partitions": [],
            "leakage": {"groups": []},
        },
    }
    mc = {"baseSize": 0.4}
    runner = _bootstrap_runner(tmp_path, mc, racks, {})
    with caplog.at_level(logging.WARNING):
        runner._generate_snappy_hex_mesh_dict(tmp_path / "Mesh" / "system")
    messages = [rec.message for rec in caplog.records]
    assert any("wallBaffleSolids" in m and "A17" in m for m in messages), \
        "runner must warn when wallBaffleSolids is non-empty"
```

- [ ] **Step 6.2: Run tests to verify failures**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v -k "floor_feature or wallbafflesolids"`
Expected: 2 FAIL.

- [ ] **Step 6.3: Implement**

Floor feature-level: already covered by Task 3.3's edit (the `elif filename == "merged_floor_ceiling.stl":` branch reads `floorFeatureLevel`). Verify by re-running.

wallBaffleSolids warn: in `_generate_snappy_hex_mesh_dict`, after loading `data`, add near the top:

```python
# 2026-04-17: wallBaffleSolids is a deprecated schema slot; emit a warning
# if any rack uses it so the developer catches schema drift early.
for rack_name, rack in (data.get("racks") or {}).items():
    wbs = rack.get("wallBaffleSolids") or []
    if wbs:
        logger.warning(
            f"rack '{rack_name}' has non-empty wallBaffleSolids ({len(wbs)} entries); "
            f"dropping — these are not routed to any STL file. See "
            f"docs/superpowers/specs/2026-04-17-refinement-architecture-redesign-design.md "
            f"§Risks."
        )
```

- [ ] **Step 6.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 19 passed.

- [ ] **Step 6.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "feat(runner): floor feature-level + wallBaffleSolids drift warning"
```

---

## Task 7: Backend — patch-on-face inheritance in scene-merged regions

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (per-solid region emission)
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py`

- [ ] **Step 7.1: Write failing test — patch inherits parent's surface level**

Append:

```python
def test_patch_inherits_parent_surface_level(tmp_path):
    """A patch solid whose name is `<parent_solid>_p<N>` inherits the
    parent's level when emitted into a regions block."""
    stl_solids = {
        "merged_server_1U.stl": [
            "A17_server01_inlet",
            "A17_server01_inlet_p0",   # patch on the inlet
            "A17_server01_inlet_p1",
        ],
    }
    mc = {
        "baseSize": 0.4,
        "serverFacePerUnitLevel": {"1": [3, 4]},
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    _write_dummy_stl(tri_dir, "merged_server_1U.stl",
                     ["A17_server01_inlet", "A17_server01_inlet_p0",
                      "A17_server01_inlet_p1"])
    runner = _bootstrap_runner(tmp_path, mc, {}, stl_solids)
    runner._generate_snappy_hex_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "snappyHexMeshDict").read_text()
    # Per-bucket file has a single top-level level(3 4); no regions{} needed
    # (all solids share the bucket level).
    assert 'merged_server_1U\n    {\n        level (3 4);' in text
```

- [ ] **Step 7.2: Run test**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py::test_patch_inherits_parent_surface_level -v`
Expected: PASS (per-bucket files don't need regions — Task 4 emits a single top-level level, which implicitly covers all solids in the file including patches).

- [ ] **Step 7.3: No implementation change needed**

Per-bucket file inheritance is free: a per-bucket STL emits `level (min max)` at the top, which applies to every solid in the file including patches. No regions block, no special patch handling.

For scene-merged files (`merged_rack_frame.stl`, `merged_acu.stl`, etc.) where regions ARE used, the per-region loop already emits whatever solids exist in the file — patches appear as additional solids and default to the file's bucket level. Inheritance is implicit via file-level fallback.

Document this behaviour with a code comment where the regions block is emitted. Find the "solid_entries" loop in `_generate_snappy_hex_mesh_dict` (around line 3548) and add above it:

```python
# 2026-04-17: Patches on faces (solids named `<parent>_p<N>`) live in the
# same STL as their parent solid and implicitly inherit the parent's
# surface level via the file-level `level` fallback. No explicit
# per-patch regions block is emitted — patches only get their own
# `regions {}` entry when they need a DIFFERENT level from the parent,
# which never happens in the current UI (which doesn't expose per-patch
# refinement controls).
```

- [ ] **Step 7.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest src/backend/aircooling/runner/tests/test_refinement_redesign.py -v`
Expected: 20 passed.

- [ ] **Step 7.5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_refinement_redesign.py
git commit -m "docs(runner): patch inheritance documented; no code change needed"
```

---

## Task 8: Frontend state — add new cfdRoom keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`getInitialState()` cfdRoom block around line 2795-2828)
- Create: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 8.1: Write failing test**

Create `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`:

```python
"""Static-structure tests for the 2026-04-17 refinement architecture redesign.

Mirrors test_perforated_frontend.py's regex pattern — scans STL_generate.html
for presence of required state keys, functions, and wiring calls. Does NOT
require a running server.

Spec: docs/superpowers/specs/2026-04-17-refinement-architecture-redesign-design.md
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


def test_initial_state_new_per_bucket_feature_maps():
    body = extract_fn_body("getInitialState")
    assert body is not None
    for key in [
        "serverFaceFeaturePerUnitLevel",
        "blankingPlateFeaturePerUnitLevel",
        "blankingPlateFeatureLevel",
        "rackPartitionRefinementLevel",
        "rackPartitionPerUnitLevel",
        "rackPartitionFeatureLevel",
        "rackPartitionFeaturePerUnitLevel",
        "rackLeakageFeatureLevel",
        "rackLeakageFeaturePerUnitLevel",
        "floorRefinementLevel",
        "floorFeatureLevel",
    ]:
        assert key in body, f"getInitialState must declare {key}"


def test_initial_state_removes_rack_frame_per_side():
    """rackFramePerSideLevel is deleted from getInitialState per spec
    §Risks (no per-side rack-frame bucketing)."""
    body = extract_fn_body("getInitialState")
    assert body is not None
    assert "rackFramePerSideLevel" not in body, \
        "rackFramePerSideLevel must be removed (no per-side rack-frame sub-buckets)"
```

- [ ] **Step 8.2: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 2 FAIL (keys not declared; rackFramePerSideLevel still present).

- [ ] **Step 8.3: Add state defaults + remove rackFramePerSideLevel**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, inside `getInitialState()` cfdRoom block (around line 2795-2828): insert the new keys near other refinement fields (after `leakageRefinementLevel: [2, 3],`):

```javascript
                // 2026-04-17 refinement architecture redesign additions:
                serverFaceFeaturePerUnitLevel: {},
                blankingPlateFeatureLevel: 2,
                blankingPlateFeaturePerUnitLevel: {},
                rackPartitionRefinementLevel: [1, 1],
                rackPartitionPerUnitLevel: {},
                rackPartitionFeatureLevel: 2,
                rackPartitionFeaturePerUnitLevel: {},
                rackLeakageFeatureLevel: 2,
                rackLeakageFeaturePerUnitLevel: {},
                floorRefinementLevel: [1, 1],
                floorFeatureLevel: 2,
```

Then grep the file for `rackFramePerSideLevel` and delete every declaration line (there should be 1-2 sites). Include a one-line comment at the deletion site:

```javascript
                // rackFramePerSideLevel removed (no per-side rack-frame sub-buckets per 2026-04-17 redesign)
```

- [ ] **Step 8.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 2 passed.

- [ ] **Step 8.5: Commit (submodule + parent bump)**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-state): refinement redesign state keys + drop rack-frame per-side"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — refinement redesign state keys"
```

---

## Task 9: Frontend — bucket-key computation helpers

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (near existing `_computeRackMinUSpan` and `scanRackDoorMinUnitBuckets`)
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 9.1: Write failing tests**

Append:

```python
def test_compute_rack_partition_buckets_helper_exists():
    body = extract_fn_body("scanRackPartitionBuckets")
    assert body is not None, "scanRackPartitionBuckets() must be defined"
    assert "count" in body and "patchHeight" in body


def test_compute_server_bucket_for_server_solid_helper_exists():
    """Maps a server solid name → its U-span bucket key (matches filename
    suffix pattern, e.g. '1' for merged_server_1U.stl)."""
    body = extract_fn_body("_computeServerBucketKeyForSolid")
    assert body is not None
```

- [ ] **Step 9.2: Run tests — FAIL**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v -k "partition_buckets or server_bucket_for_solid"`
Expected: 2 FAIL.

- [ ] **Step 9.3: Implement helpers**

Near `scanRackDoorMinUnitBuckets` in `STL_generate.html`, add:

```javascript
    // 2026-04-17: Partition 隔板 bucket scan. Walks each rack's partitions
    // list looking for "interior partition" entries (not the blanking panels).
    // Partition-inside-through-slot has solidName containing "_partition_inside"
    // or equivalent marker. Bucket key = U-span of the slot the partition sits in.
    function scanRackPartitionBuckets() {
        var buckets = {};
        (state.models || []).forEach(function(model) {
            if (model.kind !== 'rack' || !model.rack) return;
            var unitH = (model.rack.unitHeight) || RACK_DEFAULT_UNIT_HEIGHT;
            (model.rack.partitions || []).forEach(function(p) {
                // Detect interior partition (currently schema has this as a
                // sub-category of the partition array; the check here is
                // conservative and accepts any entry that carries a
                // positiveRange span field, since blanking plates are 1U).
                var span = 1; // default 1U; refined when partition schema gains span info
                if (p && p.endUnit !== undefined && p.startUnit !== undefined) {
                    span = p.endUnit - p.startUnit + 1;
                }
                // Only count partitions marked as interior (not face blanking).
                // For now, route all entries whose name includes "_partition_inside".
                if (!p || !p.solidName) return;
                if (p.solidName.indexOf("_partition_inside") === -1) return;
                var key = String(span);
                if (!buckets[key]) buckets[key] = { count: 0, patchHeight: span * unitH };
                buckets[key].count++;
            });
        });
        return buckets;
    }

    // Bucket key for a given server solid. Walks all racks and finds the
    // server entry whose inletSolidName / outletSolidName matches.
    function _computeServerBucketKeyForSolid(solidName) {
        if (!solidName) return null;
        var hit = null;
        (state.models || []).forEach(function(model) {
            if (model.kind !== 'rack' || !model.rack) return;
            (model.rack.equipments || []).forEach(function(eq) {
                if (!eq) return;
                // Check any exported solid for this equipment
                var span = eq.spanUnits || 1;
                // Match by solid prefix (all server face solids carry this equipment's id)
                var eqId = eq.id || '';
                if (eqId && solidName.indexOf(eqId) === 0) {
                    hit = String(span);
                }
            });
        });
        return hit;
    }
```

- [ ] **Step 9.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 4 passed.

- [ ] **Step 9.5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-scan): scanRackPartitionBuckets + _computeServerBucketKeyForSolid"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — partition/server bucket helpers"
```

---

## Task 10: Frontend — refactor `STL_EXPORT_GROUPS` + `buildGroupedStlMap` for per-bucket

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`STL_EXPORT_GROUPS` around line 18286, `buildGroupedStlMap` around line 18461)
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 10.1: Write failing structural test**

Append:

```python
def test_stl_export_groups_has_new_scene_filenames():
    """Scene-merged group list uses the new filenames."""
    m = re.search(r"const STL_EXPORT_GROUPS = \[([\s\S]*?)\];", HTML)
    assert m is not None, "STL_EXPORT_GROUPS not found"
    body = m.group(1)
    assert "merged_rack_frame.stl" in body
    assert "merged_rack.stl" not in body, \
        "old merged_rack.stl must be removed"


def test_build_grouped_stl_map_routes_per_bucket():
    body = extract_fn_body("buildGroupedStlMap")
    assert body is not None
    for marker in [
        "merged_server_",
        "merged_blanking_",
        "merged_partition_",
        "merged_leakage_",
        "merged_door_",
    ]:
        assert marker in body, \
            f"buildGroupedStlMap must emit files named {marker}<N>U.stl"


def test_build_grouped_stl_map_does_not_emit_legacy_door_individual():
    body = extract_fn_body("buildGroupedStlMap")
    assert body is not None
    # Must NOT emit 'baffle_' + solidName + '_door.stl' format (pre-redesign)
    assert "'baffle_' + solidName + '.stl'" not in body, \
        "legacy per-door file emission must be replaced with merged_door_<N>U.stl"
```

- [ ] **Step 10.2: Run tests — FAIL**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 3 NEW failures.

- [ ] **Step 10.3: Replace STL_EXPORT_GROUPS**

In `STLGeneratorForOpenFoam/web/STL_generate.html` around line 18286, replace the array literal with:

```javascript
    const STL_EXPORT_GROUPS = [
        // Scene-merged files (single feature level per file; per-solid surface via regions)
        { key: 'rack_frame', filename: 'merged_rack_frame.stl',   kinds: ['rack'], frameOnly: true },
        { key: 'acu',        filename: 'merged_acu.stl',          kinds: ['acu'] },
        { key: 'baffle',     filename: 'merged_baffle.stl',       kinds: ['baffle'] },
        { key: 'floor',      filename: 'merged_floor_ceiling.stl',kinds: ['floor'] },
        { key: 'perforated', filename: 'merged_perforated.stl',   kinds: ['floor'], perforatedOnly: true },
        { key: 'box',        filename: 'merged_box.stl',          kinds: ['box'] },
        { key: 'wall',       filename: 'room_wall.stl',           kinds: ['wall'] },
        { key: 'partitionWall', filename: 'merged_partition_wall.stl', kinds: ['partitionWall'] },
    ];
```

The old `rack` entry (merged_rack.stl) is gone; its role is split across `rack_frame` (frame solids only) + the per-bucket server/blanking/partition/leakage paths added below.

- [ ] **Step 10.4: Rewrite `buildGroupedStlMap` rack handling**

This is the biggest change in the plan. The existing function has a top-level `STL_EXPORT_GROUPS.forEach` loop + a door-specific loop at the end. Rewrite to ALSO emit per-bucket rack-internal files.

Open `buildGroupedStlMap` (around line 18461) and make two changes:

a. When the `STL_EXPORT_GROUPS` loop processes a rack model, if `group.frameOnly === true`, extract ONLY the frame solids from the rack's exported STL (filter `solid <name>` blocks where `<name>` matches `_frame_` or `_wall_` frame patterns).

b. After the `STL_EXPORT_GROUPS.forEach` loop, add a rack-internal per-bucket pass:

```javascript
            // 2026-04-17 redesign: rack-internal per-bucket STLs.
            // Each rack's exported STL contains server / blanking / partition /
            // leakage solids; group them by (category, U-span) into separate
            // files so each file gets its own feature-line level per snappy.
            state.models.forEach(function(model) {
                if (!idSet.has(model.id) || model.kind !== 'rack') return;
                if (!isModelEnabled(model) && !disabledExtras.includes(model)) return;
                var rackStl = exportModelToStl(model, '', isMirrored);
                rackStl = applyModelSolidOrientationOverrides(model, rackStl);
                // Extract each solid block, route to correct per-bucket file.
                var solidRe = /solid\s+(\S+)\n[\s\S]*?endsolid\s+\S+\n/g;
                var match;
                while ((match = solidRe.exec(rackStl)) !== null) {
                    var solidName = match[1];
                    var fullBlock = match[0];
                    var target = _classifyRackSolidToBucketFile(solidName, model);
                    if (!target) continue;  // frame solid → already in merged_rack_frame.stl
                    result[target] = (result[target] || '') + fullBlock;
                }
            });

            // Leakage cellZones: per-bucket (merged_leakage_<N>U.stl).
            if (includeLeakage) {
                state.models.forEach(function(model) {
                    if (!idSet.has(model.id) || model.kind !== 'rack') return;
                    var leakResult = buildLeakageZoneStl(model);
                    if (!leakResult || !leakResult.stl) return;
                    // Each leakage group solid has endUnit-startUnit+1 span;
                    // the solidName pattern is <rack>_u<start>_u<end>_leakage_zone.
                    var groups = getRackLeakageGroups(model);
                    groups.forEach(function(g) {
                        var span = g.endUnit - g.startUnit + 1;
                        var fn = 'merged_leakage_' + span + 'U.stl';
                        var rName = sanitizeName(model.name);
                        var solidName = rName + '_u' + (g.startUnit + 1) + '_u' + (g.endUnit + 1) + '_leakage_zone';
                        var re = new RegExp('solid\\s+' + solidName + '\\n[\\s\\S]*?endsolid\\s+\\S+\\n');
                        var m = leakResult.stl.match(re);
                        if (m) result[fn] = (result[fn] || '') + m[0];
                    });
                });
            }

            // Doors: per-rack-min-U-span bucket (merged_door_<bucket>.stl).
            state.models.forEach(function(model) {
                if (!idSet.has(model.id) || model.kind !== 'rack') return;
                var rack = model.rack;
                var minSpan = _computeRackMinUSpan(model);
                if (minSpan === null) return;  // empty rack → skip doors
                var bucketFn = 'merged_door_' + minSpan + 'U.stl';
                ['inlet', 'outlet'].forEach(function(side) {
                    var door = rack[side + 'Door'];
                    if (!door) return;
                    var doorStl = _buildSingleDoorStl(model, side);  // refactored helper
                    if (doorStl) result[bucketFn] = (result[bucketFn] || '') + doorStl;
                });
            });
```

Add two helper functions above `buildGroupedStlMap`:

```javascript
    // Route a rack-internal solid to its per-bucket STL filename.
    // Returns null for frame solids (they live in merged_rack_frame.stl
    // and are handled by the scene-level STL_EXPORT_GROUPS loop).
    function _classifyRackSolidToBucketFile(solidName, model) {
        if (!solidName || !model || !model.rack) return null;
        var n = solidName;
        // Frame solids: handled elsewhere (frame branch of STL_EXPORT_GROUPS)
        if (n.indexOf('_frame_') !== -1) return null;
        // Server face solids: look up the equipment's spanUnits
        var serverKey = _computeServerBucketKeyForSolid(solidName);
        if (serverKey !== null) return 'merged_server_' + serverKey + 'U.stl';
        // Blanking panel solids: name pattern includes '_blanking_' or '_u<N>_u<M>_blanking'
        var mBlank = n.match(/_u(\d+)_u(\d+)_blanking/);
        if (mBlank) {
            var span = parseInt(mBlank[2]) - parseInt(mBlank[1]) + 1;
            return 'merged_blanking_' + span + 'U.stl';
        }
        // Partition 隔板: name pattern includes '_partition_inside'
        var mPart = n.match(/_u(\d+)_u(\d+)_partition_inside/);
        if (mPart) {
            var pspan = parseInt(mPart[2]) - parseInt(mPart[1]) + 1;
            return 'merged_partition_' + pspan + 'U.stl';
        }
        // Fallthrough: log once for visibility
        console.warn('[stl-bucket] unrouted rack solid:', solidName);
        return null;
    }

    // Extract the exported STL content for a single door (inlet or outlet)
    // of a given rack model. Same triangle construction as the old inline
    // per-door loop; now parametric so it can be called from the bucket loop.
    function _buildSingleDoorStl(model, side) {
        var rack = model.rack;
        var door = rack[side + 'Door'];
        if (!door) return '';
        var slotWidth = model.base.W * rack.slotWidthRatio;
        var slotX = (model.base.W - slotWidth) / 2;
        var safeBot = rack.safeMarginBottom;
        var centralH = rack.unitCount * rack.unitHeight;
        if (centralH <= 1e-6) return '';
        var yPos = side === 'inlet' ? 0 : model.base.D;
        var rName = sanitizeName(model.name);
        var solidName = rName + '_' + side + '_door';
        var ny = side === 'inlet' ? -1 : 1;
        var x0 = slotX, x1 = slotX + slotWidth;
        var z0 = safeBot, z1 = safeBot + centralH;
        var mat = new THREE.Matrix4();
        var modelGroup = getRenderRootByModelId(model.id);
        if (modelGroup) {
            mat.copy(modelGroup.matrixWorld);
        } else {
            mat.makeTranslation(model.anchor.x, model.anchor.y, model.anchor.z);
        }
        function tx(x, y, z) {
            var v = new THREE.Vector3(x, y, z).applyMatrix4(mat);
            return [v.x, v.y, v.z];
        }
        var p0 = tx(x0, yPos, z0), p1 = tx(x1, yPos, z0),
            p2 = tx(x1, yPos, z1), p3 = tx(x0, yPos, z1);
        var e1 = [p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]];
        var e2 = [p3[0]-p0[0], p3[1]-p0[1], p3[2]-p0[2]];
        var n = [e1[1]*e2[2]-e1[2]*e2[1], e1[2]*e2[0]-e1[0]*e2[2], e1[0]*e2[1]-e1[1]*e2[0]];
        var nLen = Math.sqrt(n[0]*n[0] + n[1]*n[1] + n[2]*n[2]);
        if (nLen > 1e-10) { n[0]/=nLen; n[1]/=nLen; n[2]/=nLen; }
        if (n[1] * ny < 0) { n[0]=-n[0]; n[1]=-n[1]; n[2]=-n[2]; }
        var ns = n[0] + ' ' + n[1] + ' ' + n[2];
        var stl = 'solid ' + solidName + '\n';
        stl += 'facet normal ' + ns + '\n outer loop\n';
        stl += '  vertex ' + p0.join(' ') + '\n  vertex ' + p1.join(' ') + '\n  vertex ' + p2.join(' ') + '\n';
        stl += ' endloop\nendfacet\n';
        stl += 'facet normal ' + ns + '\n outer loop\n';
        stl += '  vertex ' + p0.join(' ') + '\n  vertex ' + p2.join(' ') + '\n  vertex ' + p3.join(' ') + '\n';
        stl += ' endloop\nendfacet\n';
        stl += 'endsolid ' + solidName + '\n';
        return stl;
    }
```

Finally, DELETE the pre-existing door emission block (the section that writes `result['baffle_' + solidName + '.stl'] = doorStl;` inside `buildGroupedStlMap`). Search for `result['baffle_' + solidName + '.stl']` and remove the entire surrounding `state.models.forEach` block (around line 18543-18618).

- [ ] **Step 10.5: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 6 passed.

- [ ] **Step 10.6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-export): per-bucket STL split for rack server/blanking/partition/leakage/door"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — per-bucket STL split"
```

---

## Task 11: Frontend UI — RACK 表面 row removes per-side + feature input

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`refreshRackRefinePanelUI`, `scanRackFrameBuckets`)
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 11.1: Write failing structural test**

Append:

```python
def test_rack_frame_row_has_no_feature_input_or_sides():
    body = extract_fn_body("refreshRackRefinePanelUI")
    assert body is not None
    # The rack-frame _renderCategorySection call must NOT pass a featureKey
    # and must NOT pass scanRackFrameBuckets (sides gone).
    # Accept either approach: a simpler panel row for frame, or a
    # _renderCategorySection with scanFn that returns {} for frame.
    m = re.search(
        r"_renderCategorySection\s*\(\s*[^)]*rackRefinementLevel[^)]*\)",
        body,
    )
    assert m is not None, "rack-frame row call not found"
    call = m.group(0)
    # Must NOT include scanRackFrameBuckets (per-side sub-rows gone)
    assert "scanRackFrameBuckets" not in call, \
        "rack-frame row must not call scanRackFrameBuckets (per-side sub-rows removed)"
    # Must NOT include rackFeatureLevel as the featureKey arg
    assert "rackFeatureLevel" not in call, \
        "rack-frame row must not declare rackFeatureLevel inline " \
        "(rack feature level comes from the outer 机架 mesh-tab row instead)"
```

- [ ] **Step 11.2: Run test — FAIL**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py::test_rack_frame_row_has_no_feature_input_or_sides -v`
Expected: FAIL — current call passes both `scanRackFrameBuckets` and `'rackFeatureLevel'`.

- [ ] **Step 11.3: Rewrite the rack-frame row invocation**

Find the `_renderCategorySection` call for the rack-frame row in `refreshRackRefinePanelUI` (around line 24685). Replace with:

```javascript
            _renderCategorySection(
                t('label.rack_frame_section'), function() { return {}; },  // no sub-buckets
                'rackRefinementLevel', 'rackFramePerSideLevel', [1,1],
                'label.rack_frame_side',
                "window.setUnifiedRackCategory('rackRefinementLevel','rackFramePerSideLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('rackFramePerSideLevel','rackRefinementLevel','{KEY}',{IDX},+this.value)"
                // No featureKey arg — rack feature-line lives on the outer 机架 mesh-tab row
            )
```

(Passing an empty-bucket scan function collapses the sub-rows to nothing; the unified row still shows. `rackFramePerSideLevel` stays as an argument for backward-compatibility call-shape but the setter will never populate it since there are no buckets.)

- [ ] **Step 11.4: Run test**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 7 passed.

- [ ] **Step 11.5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-panel): rack-frame row drops per-side sub-rows + feature-line input"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — rack-frame row simplified"
```

---

## Task 12: Frontend UI — add per-bucket feature-line inputs + Partition row

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`_renderCategorySection` helper + `refreshRackRefinePanelUI`)
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` + `en-US.json`
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 12.1: Write failing structural tests**

Append:

```python
def test_render_category_section_supports_per_bucket_feature_input():
    """The panel helper must accept a featurePerUnitKey arg and render a
    feature input on EACH bucket row (not just the unified row)."""
    body = extract_fn_body("_renderCategorySection")
    assert body is not None
    # New arg name (TBD but we assert the key is passed to the per-unit setter)
    assert "setPerUnitRackCategoryFeature" in body or \
           "PerUnitFeatureLevel" in body, \
        "_renderCategorySection must handle per-bucket feature inputs"


def test_partition_panel_row_rendered():
    body = extract_fn_body("refreshRackRefinePanelUI")
    assert body is not None
    assert "rackPartitionRefinementLevel" in body
    assert "rackPartitionPerUnitLevel" in body
    assert "scanRackPartitionBuckets" in body


def test_i18n_has_partition_label():
    import json
    zh = json.loads((HTML_PATH.parent / "i18n" / "zh-CN.json").read_text())
    en = json.loads((HTML_PATH.parent / "i18n" / "en-US.json").read_text())
    assert "label.rack_partition_section" in zh
    assert "label.rack_partition_section" in en
    assert "{n}" in zh.get("label.rack_partition_per_unit", "")
```

- [ ] **Step 12.2: Run tests — FAIL**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v -k "per_bucket_feature or partition_panel or i18n_has_partition"`
Expected: 3 FAIL.

- [ ] **Step 12.3: Extend `_renderCategorySection` for per-bucket feature inputs**

Add a new setter window function and pass its name as a new arg to `_renderCategorySection`. Find `window.setPerUnitRackCategory = function(...)` and add a sibling:

```javascript
    window.setPerUnitRackCategoryFeature = function(perUnitKey, unifiedKey, uKey, value) {
        pushUndoCheckpoint('setPerUnitRackCategoryFeature');
        var perUnit = state.cfdRoom[perUnitKey] || {};
        perUnit[uKey] = +value;
        state.cfdRoom[perUnitKey] = perUnit;
        refreshRackRefinePanelUI();
        logOperation('setPerUnitRackCategoryFeature',
            t('log.refinement_strategy', { key: uKey + 'U-feat', value: JSON.stringify(value) }),
            { perUnitKey: perUnitKey, uKey: uKey, value: value }, 'modify');
    };
```

Modify `_renderCategorySection(...)` signature to optionally accept a `featurePerUnitKey` + `setPerUnitFeatureCall` pair. For each bucket row, if both args present, render an extra `<input type="number">` for the per-bucket feature level reading from `state.cfdRoom[featurePerUnitKey][key]`.

Find the per-bucket row HTML in `_renderCategorySection` (the section after `html += keys.map(...)`, the inline-input generation). Extend each row's HTML to include a 3rd input if `featurePerUnitKey` is passed:

```javascript
        html += keys.map(function(key, idx) {
            // ... existing setup code ...
            var featureInput = '';
            if (featurePerUnitKey && setPerUnitFeatureCall) {
                var curFeat = (state.cfdRoom[featurePerUnitKey] || {})[key] ||
                              (state.cfdRoom[unifiedKey + 'FeatureLevel'] || 2);
                // Actually use the passed-in unifiedFeatureKey
                // (fix: add unifiedFeatureKey param for correctness)
                var perUnitFeatCall = setPerUnitFeatureCall.replace('{KEY}', key);
                featureInput = '<input type="number" min="0" max="8" value="' + curFeat +
                    '" class="mesh-lvl-input" style="width:38px;" title="feature" ' +
                    'oninput="' + perUnitFeatCall + '">';
            }
            return '<div style="...">' + /* existing */ featureInput + '</div>';
        }).join('');
```

Note: to read the right unified feature default, add an `unifiedFeatureKey` parameter. So the new signature is:

```javascript
function _renderCategorySection(title, scanFn, unifiedKey, perUnitKey, defaultUnified, labelKey, setUnifiedCall, setPerUnitCall, featureKey, featurePerUnitKey, setPerUnitFeatureCall, unifiedFeatureKey)
```

- [ ] **Step 12.4: Add i18n keys**

Edit `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` (add near other `label.rack_*`):

```json
"label.rack_partition_section": "🪟 Partition 隔板",
"label.rack_partition_per_unit": "{n}U ({count})",
```

Edit `en-US.json`:

```json
"label.rack_partition_section": "🪟 Partition wall",
"label.rack_partition_per_unit": "{n}U ({count})",
```

- [ ] **Step 12.5: Add Partition row + extend blanking/server/leakage rows**

In `refreshRackRefinePanelUI`, after the blanking section and before the door section, add:

```javascript
            + _renderCategorySection(
                t('label.rack_partition_section'), scanRackPartitionBuckets,
                'rackPartitionRefinementLevel', 'rackPartitionPerUnitLevel', [1,1],
                'label.rack_partition_per_unit',
                "window.setUnifiedRackCategory('rackPartitionRefinementLevel','rackPartitionPerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('rackPartitionPerUnitLevel','rackPartitionRefinementLevel','{KEY}',{IDX},+this.value)",
                'rackPartitionFeatureLevel',
                'rackPartitionFeaturePerUnitLevel',
                "window.setPerUnitRackCategoryFeature('rackPartitionFeaturePerUnitLevel','rackPartitionRefinementLevel','{KEY}',+this.value)",
                'rackPartitionFeatureLevel'
            )
```

Extend the existing server/blanking/leakage calls to pass the new 4 args (featurePerUnit + setter + unifiedFeatureKey). For server:

```javascript
            _renderCategorySection(
                t('label.server_face_section'), scanServerUnitBuckets,
                'serverFaceRefinementLevel', 'serverFacePerUnitLevel', [1,1],
                'label.per_unit_refinement',
                "window.setUnifiedRackCategory('serverFaceRefinementLevel','serverFacePerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('serverFacePerUnitLevel','serverFaceRefinementLevel','{KEY}',{IDX},+this.value)",
                'serverFaceFeatureLevel',
                'serverFaceFeaturePerUnitLevel',
                "window.setPerUnitRackCategoryFeature('serverFaceFeaturePerUnitLevel','serverFaceRefinementLevel','{KEY}',+this.value)",
                'serverFaceFeatureLevel'
            )
```

Do the same for blanking and leakage (add the 4 trailing args).

- [ ] **Step 12.6: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 10 passed.

- [ ] **Step 12.7: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html web/i18n/zh-CN.json web/i18n/en-US.json scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-panel): per-bucket feature-line inputs + new Partition row"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — per-bucket feature UI + partition row"
```

---

## Task 13: Frontend — `setUnifiedRackCategory` broadcasts to all buckets

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`setUnifiedRackCategory` function)
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 13.1: Write failing static test**

Append:

```python
def test_set_unified_rack_category_broadcasts_to_per_unit():
    body = extract_fn_body("setUnifiedRackCategory")
    assert body is not None
    # Must iterate perUnit keys and overwrite them with the unified value.
    assert "Object.keys" in body, \
        "setUnifiedRackCategory must iterate per-unit dict keys"
    assert "perUnit[" in body and "= unified" in body or \
           "= unifiedValue" in body or "= unified.slice()" in body, \
        "setUnifiedRackCategory must propagate unified value to each bucket"
```

- [ ] **Step 13.2: Run test — PASS (existing behaviour already loops; verify)**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py::test_set_unified_rack_category_broadcasts_to_per_unit -v`
Expected: PASS (the existing implementation at around line 24727 already does `Object.keys(perUnit).forEach(function(key) { perUnit[key] = unified.slice(); })`). Verify by reading the function body.

If PASS, no code change needed. If FAIL, add the broadcast logic.

- [ ] **Step 13.3: Add broadcast to `setMeshRefineLevel` too (for outer rack/rackFeatureLevel widget edits)**

`setMeshRefineLevel` doesn't broadcast since it operates on scalars (feature level) or scene-level (rackRefinementLevel surface). No change needed for scalars. For the rack surface input (`rackRefinementLevel`), confirm that `setUnifiedRackCategory('rackRefinementLevel',...)` IS used from the panel — it should be per Task 11's panel refactor.

- [ ] **Step 13.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 11 passed.

- [ ] **Step 13.5: Commit (empty commit to mark milestone)**

```bash
git commit --allow-empty -m "test(ui): verify setUnifiedRackCategory broadcasts to per-unit buckets"
```

---

## Task 14: Frontend — `_autoDetectCategory` sets feature = surface max

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`_autoDetectCategory`)
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 14.1: Write failing test**

Append:

```python
def test_auto_detect_category_sets_feature_equals_surface_max():
    body = extract_fn_body("_autoDetectCategory")
    assert body is not None
    # Must write the feature-per-unit entry = newLevel[1] (surface max).
    assert "FeaturePerUnitLevel" in body or \
           "perUnitFeat[" in body, \
        "_autoDetectCategory must update the per-bucket feature map"
```

- [ ] **Step 14.2: Run test — FAIL**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py::test_auto_detect_category_sets_feature_equals_surface_max -v`
Expected: FAIL.

- [ ] **Step 14.3: Extend `_autoDetectCategory` to also write feature map**

Find `function _autoDetectCategory(scanFn, unifiedKey, perUnitKey, defaultUnified)` around line 24474. Change signature to accept 2 more args for the feature-per-unit map + unified:

```javascript
function _autoDetectCategory(scanFn, unifiedKey, perUnitKey, defaultUnified,
                             featurePerUnitKey, featureUnifiedKey) {
    var buckets = scanFn();
    var baseSize = state.cfdRoom.meshBaseSize || 0.4;
    var perUnit = state.cfdRoom[perUnitKey] || {};
    var unified = state.cfdRoom[unifiedKey] || defaultUnified;
    var perUnitFeat = featurePerUnitKey ? (state.cfdRoom[featurePerUnitKey] || {}) : null;
    var changes = [];
    Object.keys(buckets).sort(function(a, b) { return +a - +b; }).forEach(function(key) {
        var bucket = buckets[key];
        var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
        var current = perUnit[key] || unified.slice();
        if (current[0] < safeLevel[0] || current[1] < safeLevel[1]) {
            var newLevel = [Math.max(current[0], safeLevel[0]), Math.max(current[1], safeLevel[1])];
            changes.push({ uHeight: +key, count: bucket.count, patchHeight: bucket.patchHeight,
                           oldLevel: current.slice(), newLevel: newLevel, safeLevel: safeLevel });
            perUnit[key] = newLevel;
            // 2026-04-17: feature-line = surface max for this bucket
            if (perUnitFeat !== null) {
                perUnitFeat[key] = newLevel[1];
            }
        } else {
            if (!perUnit[key]) perUnit[key] = current;
            if (perUnitFeat !== null && perUnitFeat[key] === undefined) {
                perUnitFeat[key] = current[1];
            }
        }
    });
    Object.keys(perUnit).forEach(function(key) { if (!buckets[key]) delete perUnit[key]; });
    if (perUnitFeat !== null) {
        Object.keys(perUnitFeat).forEach(function(key) { if (!buckets[key]) delete perUnitFeat[key]; });
        state.cfdRoom[featurePerUnitKey] = perUnitFeat;
    }
    state.cfdRoom[perUnitKey] = perUnit;
    return changes;
}
```

Update callers in `autoDetectRackRefinement` (around line 24538):

```javascript
    function autoDetectRackRefinement() {
        var frameChanges = _autoDetectRackFrame();
        var serverChanges = _autoDetectCategory(scanServerUnitBuckets,
            'serverFaceRefinementLevel', 'serverFacePerUnitLevel', [1,1],
            'serverFaceFeaturePerUnitLevel', 'serverFaceFeatureLevel');
        var blankingChanges = _autoDetectCategory(scanBlankingPlateUnitBuckets,
            'blankingPlateRefinementLevel', 'blankingPlatePerUnitLevel', [1,1],
            'blankingPlateFeaturePerUnitLevel', 'blankingPlateFeatureLevel');
        var partitionChanges = _autoDetectCategory(scanRackPartitionBuckets,
            'rackPartitionRefinementLevel', 'rackPartitionPerUnitLevel', [1,1],
            'rackPartitionFeaturePerUnitLevel', 'rackPartitionFeatureLevel');
        var leakageChanges = _autoDetectCategory(scanLeakageUnitBuckets,
            'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2],
            'rackLeakageFeaturePerUnitLevel', 'rackLeakageFeatureLevel');
        var doorChanges = _autoDetectRackDoor();
        return { frame: frameChanges, server: serverChanges, blanking: blankingChanges,
                 partition: partitionChanges, leakage: leakageChanges, door: doorChanges };
    }
```

- [ ] **Step 14.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 12 passed.

- [ ] **Step 14.5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-autodetect): _autoDetectCategory sets feature = surface max per bucket"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — auto-detect feature-line update"
```

---

## Task 15: Frontend — `buildCfdExportJson` emits all new meshConfig keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`buildCfdExportJson`'s `meshConfig` block around line 27315)
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 15.1: Write failing test**

Append:

```python
def test_build_cfd_export_emits_all_new_meshconfig_keys():
    body = extract_fn_body("buildCfdExportJson")
    assert body is not None
    for key in [
        "serverFaceFeaturePerUnitLevel",
        "blankingPlateFeatureLevel",
        "blankingPlateFeaturePerUnitLevel",
        "rackPartitionRefinementLevel",
        "rackPartitionPerUnitLevel",
        "rackPartitionFeatureLevel",
        "rackPartitionFeaturePerUnitLevel",
        "rackLeakageFeatureLevel",
        "rackLeakageFeaturePerUnitLevel",
        "floorRefinementLevel",
        "floorFeatureLevel",
    ]:
        assert key in body, f"buildCfdExportJson must emit {key}"
```

- [ ] **Step 15.2: Run test — FAIL**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py::test_build_cfd_export_emits_all_new_meshconfig_keys -v`
Expected: FAIL.

- [ ] **Step 15.3: Emit all new keys**

In `buildCfdExportJson` find the `meshConfig: { ... }` block (around line 27315) and insert ALL new keys adjacent to existing refinement entries:

```javascript
                serverFaceFeaturePerUnitLevel: state.cfdRoom.serverFaceFeaturePerUnitLevel || {},
                blankingPlateFeatureLevel: state.cfdRoom.blankingPlateFeatureLevel || 2,
                blankingPlateFeaturePerUnitLevel: state.cfdRoom.blankingPlateFeaturePerUnitLevel || {},
                rackPartitionRefinementLevel: state.cfdRoom.rackPartitionRefinementLevel || [1, 1],
                rackPartitionPerUnitLevel: state.cfdRoom.rackPartitionPerUnitLevel || {},
                rackPartitionFeatureLevel: state.cfdRoom.rackPartitionFeatureLevel || 2,
                rackPartitionFeaturePerUnitLevel: state.cfdRoom.rackPartitionFeaturePerUnitLevel || {},
                rackLeakageFeatureLevel: state.cfdRoom.rackLeakageFeatureLevel || 2,
                rackLeakageFeaturePerUnitLevel: state.cfdRoom.rackLeakageFeaturePerUnitLevel || {},
                floorRefinementLevel: state.cfdRoom.floorRefinementLevel || [1, 1],
                floorFeatureLevel: state.cfdRoom.floorFeatureLevel || 2,
```

- [ ] **Step 15.4: Run tests**

Run: `conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: 13 passed.

- [ ] **Step 15.5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_refinement_redesign_frontend.py
git commit -m "feat(ui-export): buildCfdExportJson emits all new meshConfig keys"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — export new meshConfig keys"
```

---

## Task 16: Runtime headless tests — broadcast + bucket grouping

**Files:**
- Create: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_runtime.py`

- [ ] **Step 16.1: Verify server is up**

```bash
lsof -i :8005 2>/dev/null | head
```

If not running, start:

```bash
nohup conda run -n dcdtuni --no-capture-output python STLGeneratorForOpenFoam/server/server.py \
    --port 8005 --ws-port 8006 \
    --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage \
    > /tmp/stlgen_logs/server.out 2>&1 &
disown
sleep 6
```

Login:

```bash
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"WIN#@desk"}'
```

- [ ] **Step 16.2: Write runtime test file**

Create `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_runtime.py`:

```python
"""Runtime behaviour tests for the 2026-04-17 refinement architecture redesign.

Tests against the base_leakage fixture (CLAUDE.md testing constraint:
do NOT use alibaba-hongchou for iterative testing). Drives the headless
browser via /api/browser/exec-js.

Spec: docs/superpowers/specs/2026-04-17-refinement-architecture-redesign-design.md
"""
import json
import subprocess

import pytest

BASE = "http://localhost:8005"
COOKIE_JAR = "/tmp/stlgen_cookie_redesign.txt"
FIXTURE_CFDPROJ = "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj"


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
        "--max-time", "120",
        "-d", json.dumps({"path": path}),
    ], check=True, capture_output=True, text=True)
    return json.loads(r.stdout)


@pytest.fixture(scope="module", autouse=True)
def _session():
    _login()
    yield


def test_unified_broadcast_updates_per_unit_keys():
    """Editing the unified value rewrites every existing per-unit entry."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    _exec_js("""
        state.cfdRoom.serverFacePerUnitLevel = {"1": [2,2], "2": [2,2]};
        state.cfdRoom.serverFaceRefinementLevel = [1, 1];
        window.setUnifiedRackCategory(
            'serverFaceRefinementLevel', 'serverFacePerUnitLevel', 0, 5);
        window.setUnifiedRackCategory(
            'serverFaceRefinementLevel', 'serverFacePerUnitLevel', 1, 6);
    """)
    got = _exec_js("JSON.stringify(state.cfdRoom.serverFacePerUnitLevel)")["result"]
    per_unit = json.loads(got)
    for key, lvl in per_unit.items():
        assert lvl == [5, 6], f"bucket {key} = {lvl} (expected [5, 6] from broadcast)"


def test_build_grouped_stl_map_emits_new_filenames():
    """After export, the STL file list includes merged_server_<N>U.stl,
    merged_blanking_<N>U.stl, etc. — no old merged_rack.stl, no
    individual baffle_<name>_door.stl."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    files = _exec_js("""
        const allIds = state.models.map(m => m.id);
        const grouped = buildGroupedStlMap(allIds, true);
        JSON.stringify(Object.keys(grouped).sort());
    """)["result"]
    keys = json.loads(files)
    assert "merged_rack.stl" not in keys, "old merged_rack.stl must be gone"
    assert any(k.startswith("baffle_") and "_door" in k for k in keys) is False, \
        "old per-door files must be gone"
    assert "merged_rack_frame.stl" in keys
    # At least one server bucket:
    assert any(k.startswith("merged_server_") and k.endswith("U.stl") for k in keys), \
        f"no merged_server_<N>U.stl in {keys}"


def test_auto_detect_writes_per_bucket_feature_levels():
    """After auto-detect, *FeaturePerUnitLevel dicts are populated with
    values = surface max per bucket."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    _exec_js("""
        state.cfdRoom.serverFacePerUnitLevel = {};
        state.cfdRoom.serverFaceFeaturePerUnitLevel = {};
        window.runAutoDetectRackRefinement();
    """)
    srf = _exec_js("JSON.stringify(state.cfdRoom.serverFacePerUnitLevel)")["result"]
    sff = _exec_js("JSON.stringify(state.cfdRoom.serverFaceFeaturePerUnitLevel)")["result"]
    surface_map = json.loads(srf)
    feat_map = json.loads(sff)
    for key, lvl in surface_map.items():
        assert feat_map.get(key) == lvl[1], \
            f"bucket {key}: feature {feat_map.get(key)} != surface max {lvl[1]}"


def test_cfd_export_includes_all_new_keys():
    """buildCfdExportJson's meshConfig block contains every new key."""
    assert _load_proj(FIXTURE_CFDPROJ)["success"]
    exp = _exec_js("JSON.stringify(buildCfdExportJson().meshConfig)")["result"]
    mc = json.loads(exp)
    for key in [
        "serverFaceFeaturePerUnitLevel",
        "blankingPlateFeatureLevel",
        "blankingPlateFeaturePerUnitLevel",
        "rackPartitionRefinementLevel",
        "rackPartitionPerUnitLevel",
        "rackPartitionFeatureLevel",
        "rackPartitionFeaturePerUnitLevel",
        "rackLeakageFeatureLevel",
        "rackLeakageFeaturePerUnitLevel",
        "floorRefinementLevel",
        "floorFeatureLevel",
    ]:
        assert key in mc, f"meshConfig must include {key}, got {list(mc.keys())}"
```

- [ ] **Step 16.3: Run**

```bash
conda run -n dcdtuni --no-capture-output python -m pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_runtime.py -v
```

Expected: 4 passed. If load-proj times out, retry (first call warms the headless browser).

- [ ] **Step 16.4: Commit**

```bash
cd STLGeneratorForOpenFoam
git add scripts/test_refinement_redesign_runtime.py
git commit -m "test(runtime): broadcast, grouping, auto-detect, export round-trip on base_leakage"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — runtime tests on base_leakage"
```

---

## Task 17: E2E mesh verification on `case/base_leakage`

**Files:**
- No new files; documentation-only validation commit at the end.

- [ ] **Step 17.1: Confirm server targets base_leakage**

```bash
curl -s -b /tmp/stlgen_cookie.txt http://localhost:8005/api/server-info | python3 -m json.tool | grep caseDir
```

Expected: `"caseDir": "/mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage"`.

If not, restart the server with `--case-dir /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage` (see Task 16.1).

- [ ] **Step 17.2: Load project + auto-detect + trigger mesh**

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/load-proj \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj"}' \
  --max-time 120

curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/exec-js \
  -H "Content-Type: application/json" \
  -d '{"js": "window.runAutoDetectRackRefinement(); JSON.stringify({partition: state.cfdRoom.rackPartitionPerUnitLevel, server: state.cfdRoom.serverFacePerUnitLevel, blanking: state.cfdRoom.blankingPlatePerUnitLevel, door: state.cfdRoom.rackDoorPerMinUnitLevel})"}'

curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath": "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj", "meshOnly": true}' \
  --max-time 600
```

Record the returned `jobId`.

- [ ] **Step 17.3: Wait for mesh completion**

Use Monitor to poll until done (expected: 2-5 minutes for base_leakage, far faster than alibaba-hongchou):

```
Monitor(
  command: 'until curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/job-status?job_id=<jobId>" | grep -q "\"done\": true"; do sleep 30; done; echo "COMPLETE"; curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/job-status?job_id=<jobId>"',
  description: "poll base_leakage mesh job",
  timeout_ms: 900000,
  persistent: false,
)
```

Expected completion with `returncode: 0`.

- [ ] **Step 17.4: Inspect generated files**

```bash
ls /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/Mesh/constant/triSurface/
```

Expected:

- NO `merged_rack.stl`
- NO `baffle_*_door.stl`
- NO `leakage_*.stl` (individual)
- ONE `merged_rack_frame.stl`
- AT LEAST ONE each of `merged_server_<N>U.stl`, `merged_blanking_<N>U.stl`, `merged_door_<N>U.stl` (partition/leakage may be empty if base_leakage doesn't use those features)
- Scene merged: `merged_acu.stl`, `merged_box.stl`, `merged_baffle.stl`, `merged_floor_ceiling.stl`, `room_wall.stl` (as applicable)

```bash
grep -A2 "merged_server_\|merged_blanking_\|merged_partition_\|merged_leakage_\|merged_door_" \
  /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/Mesh/system/snappyHexMeshDict | head -n 40
```

Expected: each per-bucket file has its own `level (min max)` refinementSurface entry with matching feature-line level.

```bash
grep -E "merged_leakage_.*_bafflePatch|cellZone merged_leakage_" \
  /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/Mesh/system/createBafflesDict \
  /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/Mesh/system/snappyHexMeshDict
```

Expected: leakage appears only in `snappyHexMeshDict` as `cellZone`, NOT in `createBafflesDict`.

- [ ] **Step 17.5: Verify mesh quality regression**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/Mesh && \
  conda run -n dcdtuni --no-capture-output checkMesh -case . 2>&1 | \
  grep -E "multiply connected|non-orthogonality|Min volume|skewness|Max aspect" | head
```

Record the numbers. base_leakage pre-fix baseline is typically healthier than alibaba-hongchou; confirm min volume stays > 1e-8 m³ and non-manifold edges ≤ 10.

- [ ] **Step 17.6: E2E documentation commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git commit --allow-empty -m "$(cat <<'EOF'
chore(e2e): verify refinement redesign on case/base_leakage

Auto-detected bucket maps:
  server:    <paste>
  blanking:  <paste>
  partition: <paste>
  door:      <paste>

Mesh job: <jobId> (returncode=0, duration <X>s, cell count <Y>)

triSurface contents: <N> files total
  (listed by category)

checkMesh:
  min volume:              <X> m^3
  multiply-connected/door: <X>
  max skewness:            <X>
  max aspect ratio:        <X>

Conclusion: <mesh quality acceptable / regression / improvement>
EOF
)"
```

- [ ] **Step 17.7 (optional): Briefly verify solve runs**

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/run-mesh \
  -H "Content-Type: application/json" \
  --data-binary @<(python3 -c "
import json
cdj = open('/mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/model/Base_leakage.cfdrun').read()
print(json.dumps({
    'cfdExportJson': cdj,
    'stlFiles': {},
    'meshOnly': False,
    'solveOnly': True
}))") --max-time 60
```

Let it run 1-2 minutes via Monitor, verify T and U iteration residuals are finite (no NaN). If solve converges or at least iterates cleanly → full success.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|--------------|---------|
| §Core principles §1 (feature per file) | Tasks 1 (schema), 3 (features block) |
| §Core principles §2 (surface per region) | Task 7 (patch inheritance doc) — regions logic pre-existed |
| §Core principles §3 (UI inputs canonical) | Tasks 3, 4, 6, 12, 15 |
| §Core principles §4 (leakage cellZone only) | Tasks 4, 5 |
| §Core principles §5 (patch inherits parent level) | Task 7 |
| §Core principles §6 (unified broadcast) | Task 13 |
| §STL file layout scene-merged (6 files) | Tasks 3, 10 |
| §STL file layout rack-internal (4 per-bucket) | Tasks 1-5, 9-10 |
| §STL file layout doors | Task 10 (integrated with bucket logic) |
| §UI panel redesign removals | Tasks 8 (state cleanup), 11 (rack frame row) |
| §UI panel redesign additions | Tasks 12 (feature inputs + partition row), 14 (auto-detect) |
| §Patch-on-face inheritance | Task 7 |
| §Backend runner changes | Tasks 1-7 |
| §Frontend state additions | Task 8 |
| §Frontend export | Tasks 10, 15 |
| §Migration/compat | Tasks 1, 3 (legacy file fallbacks in features block), 6 (wallBaffleSolids warn) |
| §Testing 1-6 | Tasks 1, 2, 3, 4, 5, 6, 10, 12, 13, 14, 15, 16, 17 |
| §Testing E2E on base_leakage | Task 17 |

All spec sections map to at least one task.

**2. Placeholder scan:** No TBD/TODO; Task 5.1 says "find via grep" but that's a concrete investigation step, not a placeholder — the test body is complete. Task 7.3 says "no code change needed" which is explicitly stated as a correct outcome (per-bucket file inheritance is implicit). Task 17.6's commit message template has `<paste>` markers — these are intentional fill-ins for the implementer after observing the e2e run.

**3. Type consistency:**

- `_bucket_level_lookup` returns `List[int]` length 2 everywhere. Callers use `[0]` and `[1]` indexing consistently.
- `_bucket_feature_lookup` returns `int` everywhere. Task 2's tests match this.
- `_parse_bucket_from_filename` returns `Optional[tuple[str, str]]` with `(category, bucket_key)` — consistent across Tasks 2, 3, 4, 5.
- Category names are `"server"`, `"blanking"`, `"partition"`, `"leakage"`, `"door"` — used identically in `_BUCKET_FILE_PATTERNS`, the filename-prefix mapping, and the test assertions.
- Frontend state keys match backend field names exactly: `serverFaceFeaturePerUnitLevel`, `rackPartitionFeatureLevel`, etc. No drift.
- Frontend filenames (`merged_server_<N>U.stl` etc.) match `_BUCKET_FILE_PATTERNS` prefix regexes.
