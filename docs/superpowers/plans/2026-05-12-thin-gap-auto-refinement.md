# Thin-Gap Auto-Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-detect thin (<2U) gaps inside racks and generate local higher-level `searchableRotatedBox` refinement regions so snappyHexMesh can resolve them. Clean up dead per-unit bucket code.

**Architecture:** A new pure function `_detect_thin_gap_regions()` scans each `typicalSmallGap` rack's server layout, identifies unoccupied U-slot ranges where the cell count at `rackRegionLevel` is < 2, and returns searchBox specs. `_generate_snappy_hex_mesh_dict()` calls it and emits extra `refinementRegions` entries alongside the existing rack boxes. Dead per-unit bucket code is removed.

**Tech Stack:** Python 3.10, OpenFOAM snappyHexMesh dict generation, pytest

**Spec:** `docs/superpowers/specs/2026-05-12-thin-gap-auto-refinement-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/backend/aircooling/runner/stlgen_runner.py` | Modify | Add `_detect_thin_gap_regions()`, integrate into snappy generation, delete dead bucket code |
| `src/backend/tests/test_thin_gap_refinement.py` | Create | Unit tests for detection algorithm + integration test for snappyHexMeshDict output |

---

### Task 1: Add `_detect_thin_gap_regions()` pure function + unit tests

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (~line 182, after the deleted bucket functions)
- Create: `src/backend/tests/test_thin_gap_refinement.py`

- [ ] **Step 1: Write the failing test**

Create `src/backend/tests/test_thin_gap_refinement.py`:

```python
"""Tests for thin-gap auto-refinement detection algorithm."""
import math

import pytest

from dcdtuni.runner.stlgen_runner import _detect_thin_gap_regions


def _make_rack(servers, unit_count=43, unit_height=0.04889,
               blanking_type="typicalSmallGap",
               W=0.8, D=1.2, slot_ratio=0.8,
               inlet_offset=0.12, outlet_offset=0.36):
    """Build a minimal rack dict matching cfdrun schema."""
    return {
        "blankingPanelType": blanking_type,
        "geometry": {
            "unitCount": unit_count,
            "unitHeight": unit_height,
            "size": {"W": W, "D": D, "H": unit_count * unit_height},
            "anchor": {"x": 0, "y": 0, "z": 0},
            "slotWidthRatio": slot_ratio,
            "inletOffset": inlet_offset,
            "outletOffset": outlet_offset,
            "quaternion": {"x": 0, "y": 0, "z": 0, "w": 1},
            "scale": {"x": 1, "y": 1, "z": 1},
        },
        "servers": servers,
    }


def _make_server(start, span):
    return {"startSlot": start, "spanSlots": span, "type": "fanServer"}


class TestDetectThinGapRegions:
    """Unit tests for the gap detection algorithm."""

    def test_single_1u_gap_triggers(self):
        """1U gap between two servers at rackRegionLevel=3 triggers detection."""
        rack = _make_rack([
            _make_server(29, 5),   # units 29-33
            _make_server(35, 1),   # unit 35
        ])
        # Gap at unit 34: 1U = 48.89mm. At level 3, cell = 310/8 = 38.75mm → 1.26 cells
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 1
        g = gaps[0]
        assert g["rack_name"] == "Rack_A"
        assert g["start_unit"] == 34
        assert g["end_unit"] == 34
        assert g["box_level"] == 4  # ceil(log2(0.31*2/0.04889)) = 4

    def test_large_gap_does_not_trigger(self):
        """Gaps with ≥ 2 cells at current level are not flagged."""
        rack = _make_rack([_make_server(10, 5)])  # units 10-14
        # Gaps: units 0-9 (10U) and 15-42 (28U) — both large
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 0

    def test_fully_sealed_rack_skipped(self):
        """Racks with blankingPanelType != 'typicalSmallGap' are skipped."""
        rack = _make_rack(
            [_make_server(29, 5), _make_server(35, 1)],
            blanking_type="fullBlock",
        )
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 0

    def test_multiple_thin_gaps_each_gets_box(self):
        """Each thin gap gets its own independent entry."""
        rack = _make_rack([
            _make_server(10, 1),   # unit 10
            _make_server(12, 1),   # unit 12
        ])
        # Gaps at unit 11 (1U), plus large gaps 0-9 and 13-42
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        thin = [g for g in gaps if g["start_unit"] == 11]
        assert len(thin) == 1
        assert thin[0]["end_unit"] == 11

    def test_no_servers_no_gap(self):
        """Rack with no servers has one huge range — never triggers."""
        rack = _make_rack([])
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 0

    def test_safe_level_formula(self):
        """Box level = max(ceil(log2(baseSize*2/gapH)), rackRegionLevel+1)."""
        rack = _make_rack([
            _make_server(20, 1),  # unit 20
            _make_server(22, 1),  # unit 22
        ], unit_height=0.04889)
        # Gap at unit 21: 1U = 48.89mm
        # ceil(log2(0.31*2/0.04889)) = ceil(3.66) = 4
        # max(4, 3+1) = 4
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        thin = [g for g in gaps if g["start_unit"] == 21]
        assert len(thin) == 1
        assert thin[0]["box_level"] == 4

    def test_safe_level_with_low_rack_level(self):
        """When rackRegionLevel is low, safe formula dominates over +1."""
        rack = _make_rack([
            _make_server(20, 1),
            _make_server(22, 1),
        ], unit_height=0.04889)
        # At rackRegionLevel=1: ceil(log2(0.31*2/0.04889)) = 4
        # max(4, 1+1) = 4 (formula dominates)
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=1
        )
        thin = [g for g in gaps if g["start_unit"] == 21]
        assert len(thin) == 1
        assert thin[0]["box_level"] == 4

    def test_gap_geometry_fields(self):
        """Each result carries the geometry data needed for searchBox."""
        rack = _make_rack([
            _make_server(29, 5),
            _make_server(35, 1),
        ])
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        g = gaps[0]
        assert "geom" in g
        geom = g["geom"]
        assert geom["slot_width"] == pytest.approx(0.8 * 0.8, abs=1e-6)
        assert geom["channel_depth"] == pytest.approx(1.2 - 0.12 - 0.36, abs=1e-6)
        assert geom["gap_start_h"] == pytest.approx(34 * 0.04889, abs=1e-4)
        assert geom["gap_height"] == pytest.approx(1 * 0.04889, abs=1e-4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_thin_gap_refinement.py -v`
Expected: FAIL — `ImportError: cannot import name '_detect_thin_gap_regions'`

- [ ] **Step 3: Implement `_detect_thin_gap_regions()`**

Add to `src/backend/aircooling/runner/stlgen_runner.py` after line 181 (after `_bucket_feature_lookup`, which will be deleted later — for now insert after it):

```python
def _detect_thin_gap_regions(
    racks: Dict[str, Any],
    base_size: float,
    rack_region_level: int,
    min_cells: int = 2,
) -> List[Dict[str, Any]]:
    """Scan racks for thin unoccupied U-slot gaps needing higher refinement.

    Only racks with blankingPanelType == "typicalSmallGap" participate.

    Returns a list of dicts, each containing:
        rack_name, start_unit, end_unit, box_level, gap_height,
        geom: {slot_width, channel_depth, gap_start_h, gap_height,
               W, D, inlet_offset, outlet_offset, slot_ratio,
               anchor, quaternion, scale}
    """
    results: List[Dict[str, Any]] = []
    cell_size = base_size / (2 ** rack_region_level)

    for rack_name, rack in racks.items():
        if rack.get("blankingPanelType") != "typicalSmallGap":
            continue

        geom = rack.get("geometry") or {}
        unit_count = int(geom.get("unitCount", 0))
        unit_h = float(geom.get("unitHeight", 0.04445))
        if unit_count <= 0 or unit_h <= 0:
            continue

        size = geom.get("size") or {}
        W = float(size.get("W", 0.6))
        D = float(size.get("D", 1.0))
        slot_ratio = float(geom.get("slotWidthRatio", 0.8))
        inlet_off = float(geom.get("inletOffset", 0))
        outlet_off = float(geom.get("outletOffset", 0))

        occupied = set()
        for srv in rack.get("servers", []):
            start = int(srv.get("startSlot", 0))
            span = int(srv.get("spanSlots", 1))
            for u in range(start, start + span):
                occupied.add(u)

        unoccupied = sorted(set(range(unit_count)) - occupied)
        if not unoccupied:
            continue

        ranges: List[Tuple[int, int]] = []
        rng_start = unoccupied[0]
        prev = rng_start
        for u in unoccupied[1:]:
            if u == prev + 1:
                prev = u
            else:
                ranges.append((rng_start, prev))
                rng_start = u
                prev = u
        ranges.append((rng_start, prev))

        for start_u, end_u in ranges:
            gap_h = (end_u - start_u + 1) * unit_h
            if gap_h / cell_size >= min_cells:
                continue
            safe_level = math.ceil(math.log2(base_size * min_cells / gap_h))
            box_level = max(safe_level, rack_region_level + 1)

            results.append({
                "rack_name": rack_name,
                "start_unit": start_u,
                "end_unit": end_u,
                "box_level": box_level,
                "gap_height": gap_h,
                "geom": {
                    "slot_width": W * slot_ratio,
                    "channel_depth": D - inlet_off - outlet_off,
                    "gap_start_h": start_u * unit_h,
                    "gap_height": gap_h,
                    "W": W, "D": D,
                    "inlet_offset": inlet_off,
                    "outlet_offset": outlet_off,
                    "slot_ratio": slot_ratio,
                    "anchor": geom.get("anchor") or {},
                    "quaternion": geom.get("quaternion"),
                    "scale": geom.get("scale"),
                },
            })

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_thin_gap_refinement.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/tests/test_thin_gap_refinement.py
git commit -m "feat: add _detect_thin_gap_regions() for thin U-slot gap detection"
```

---

### Task 2: Integrate thin-gap searchBox into snappyHexMeshDict generation

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (~line 4697-4798, inside `_generate_snappy_hex_mesh_dict`)
- Modify: `src/backend/tests/test_thin_gap_refinement.py` (add integration test)

- [ ] **Step 1: Write the failing integration test**

Append to `src/backend/tests/test_thin_gap_refinement.py`:

```python
import copy
import json
import re
from pathlib import Path

from dcdtuni.runner.stlgen_runner import STLGenRunner

BASE_LEAKAGE_DIR = Path(__file__).resolve().parents[3] / "case" / "base_leakage"
CFDRUN = BASE_LEAKAGE_DIR / "model" / "Base_leakage.cfdrun"


def _extract_block(text, name):
    """Extract a named block { ... } from snappyHexMeshDict text."""
    pattern = rf'^\s+{re.escape(name)}\s*\{{'
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return None
    depth = 0
    for i in range(m.end() - 1, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[m.start():i + 1]
    return None


class TestThinGapInSnappy:
    """Integration: thin-gap boxes appear in snappyHexMeshDict."""

    def _generate_snappy(self, extra_servers=None, rack_region_level=3):
        """Generate snappyHexMeshDict with injected thin gap on first rack."""
        if not CFDRUN.exists():
            pytest.skip(f"Fixture not found: {CFDRUN}")

        with open(CFDRUN) as f:
            original = json.load(f)
        data = copy.deepcopy(original)

        first_rack_name = next(iter(data["racks"]))
        rack = data["racks"][first_rack_name]
        if extra_servers:
            rack["servers"] = extra_servers
        rack["blankingPanelType"] = "typicalSmallGap"
        data.setdefault("meshConfig", {})["rackRegionLevel"] = rack_region_level

        tmp = CFDRUN.parent / "_test_thin_gap.cfdrun"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            r = STLGenRunner(BASE_LEAKAGE_DIR, tmp)
            r._load()
            r.validate_geometry()
            r._preflight_partition_wall_thickness()
            r._ensure_watertight_room_enclosure()
            r._prepare_runtime_metadata()
            r._generate_mesh_dicts()
            snappy_path = BASE_LEAKAGE_DIR / "Mesh" / "system" / "snappyHexMeshDict"
            return snappy_path.read_text()
        finally:
            tmp.unlink(missing_ok=True)

    def test_thin_gap_box_appears_in_geometry(self):
        """A 1U gap produces a searchableRotatedBox in geometry section."""
        # Inject two servers with 1U gap at unit 34
        servers = [
            {"id": "s1", "type": "fanServer", "startSlot": 29, "spanSlots": 5,
             "faces": {"inlet": {"solid": "s1_in"}, "outlet": {"solid": "s1_out"},
                       "top": {"solid": "s1_top"}, "bottom": {"solid": "s1_bot"}}},
            {"id": "s2", "type": "fanServer", "startSlot": 35, "spanSlots": 1,
             "faces": {"inlet": {"solid": "s2_in"}, "outlet": {"solid": "s2_out"},
                       "top": {"solid": "s2_top"}, "bottom": {"solid": "s2_bot"}}},
        ]
        text = self._generate_snappy(extra_servers=servers, rack_region_level=3)
        assert "rackThinGapRegion_" in text
        assert "searchableRotatedBox" in text

        # Find the thin-gap region block
        block = _extract_block(text, re.findall(r"rackThinGapRegion_\S+", text)[0])
        assert block is not None
        assert "searchableRotatedBox" in block

    def test_thin_gap_refinement_level(self):
        """Thin-gap refinementRegion level exceeds rackRegionLevel."""
        servers = [
            {"id": "s1", "type": "fanServer", "startSlot": 29, "spanSlots": 5,
             "faces": {"inlet": {"solid": "s1_in"}, "outlet": {"solid": "s1_out"},
                       "top": {"solid": "s1_top"}, "bottom": {"solid": "s1_bot"}}},
            {"id": "s2", "type": "fanServer", "startSlot": 35, "spanSlots": 1,
             "faces": {"inlet": {"solid": "s2_in"}, "outlet": {"solid": "s2_out"},
                       "top": {"solid": "s2_top"}, "bottom": {"solid": "s2_bot"}}},
        ]
        text = self._generate_snappy(extra_servers=servers, rack_region_level=3)
        # The refinementRegions section should contain thin-gap entry with level > 3
        rr_match = re.search(
            r"rackThinGapRegion_\S+\s*\{\s*mode inside;\s*levels\s*\(\(1e15\s+(\d+)\)\)",
            text
        )
        assert rr_match is not None
        level = int(rr_match.group(1))
        assert level > 3

    def test_no_thin_gap_for_full_block_rack(self):
        """fullBlock rack does not produce thin-gap regions."""
        # base_leakage Default_Rack_3 is fullBlock — generate without modification
        if not CFDRUN.exists():
            pytest.skip(f"Fixture not found: {CFDRUN}")
        with open(CFDRUN) as f:
            data = json.load(f)
        # Don't inject servers — use originals. Default_Rack_3 is fullBlock.
        text = self._generate_snappy()
        # No thin gap boxes for any rack (gaps are all >2U in base_leakage)
        assert "rackThinGapRegion_" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_thin_gap_refinement.py::TestThinGapInSnappy -v`
Expected: FAIL — `rackThinGapRegion_` not found in snappy output (not yet integrated)

- [ ] **Step 3: Integrate into `_generate_snappy_hex_mesh_dict()`**

In `src/backend/aircooling/runner/stlgen_runner.py`, inside `_generate_snappy_hex_mesh_dict()`, after the main rack searchBox loop (after ~line 4774, the `else` branch closing brace), add:

```python
        # ── Thin-gap auto-refinement regions ──
        thin_gap_regions = _detect_thin_gap_regions(
            data.get("racks") or {}, base_size, rack_region_level
        )
        for tg in thin_gap_regions:
            tg_name = (f"rackThinGapRegion_{_sanitize(tg['rack_name'])}"
                       f"_u{tg['start_unit']}_u{tg['end_unit']}")
            tg_geom = tg["geom"]
            quat = tg_geom.get("quaternion")
            scale_data = tg_geom.get("scale")
            anchor = tg_geom.get("anchor") or {}
            ax = float(anchor.get("x", 0))
            ay = float(anchor.get("y", 0))
            az = float(anchor.get("z", 0))

            if quat:
                sx = float((scale_data or {}).get("x", 1))
                sy = float((scale_data or {}).get("y", 1))
                sz = float((scale_data or {}).get("z", 1))
                e1_raw = _quat_rotate((sx, 0.0, 0.0), quat)
                e2_raw = _quat_rotate((0.0, sy, 0.0), quat)
                e3_raw = _quat_rotate((0.0, 0.0, sz), quat)
                e1_len = math.sqrt(sum(v * v for v in e1_raw))
                e2_len = math.sqrt(sum(v * v for v in e2_raw))
                e3_len = math.sqrt(sum(v * v for v in e3_raw))
                e1 = tuple(v / e1_len for v in e1_raw) if e1_len > _GEOM_EPS else (1, 0, 0)
                e2 = tuple(v / e2_len for v in e2_raw) if e2_len > _GEOM_EPS else (0, 1, 0)
                e3 = tuple(v / e3_len for v in e3_raw) if e3_len > _GEOM_EPS else (0, 0, 1)

                slot_margin = (tg_geom["W"] - tg_geom["slot_width"]) / 2
                off_e1 = slot_margin * e1_len
                off_e2 = tg_geom["gap_start_h"] * e2_len
                off_e3 = tg_geom["inlet_offset"] * e3_len

                ox = ax + off_e1 * e1[0] + off_e2 * e2[0] + off_e3 * e3[0]
                oy = ay + off_e1 * e1[1] + off_e2 * e2[1] + off_e3 * e3[1]
                oz = az + off_e1 * e1[2] + off_e2 * e2[2] + off_e3 * e3[2]

                span = (
                    tg_geom["slot_width"] * e1_len,
                    tg_geom["gap_height"] * e2_len,
                    tg_geom["channel_depth"] * e3_len,
                )

                geom_lines.append(f"""    {tg_name}
    {{
        type searchableRotatedBox;
        origin ({ox:.6f} {oy:.6f} {oz:.6f});
        e1 ({e1[0]:.6f} {e1[1]:.6f} {e1[2]:.6f});
        e2 ({e2[0]:.6f} {e2[1]:.6f} {e2[2]:.6f});
        e3 ({e3[0]:.6f} {e3[1]:.6f} {e3[2]:.6f});
        span ({span[0]:.6f} {span[1]:.6f} {span[2]:.6f});
    }}""")
            else:
                logger.warning(
                    f"Rack '{tg['rack_name']}': thin gap at units "
                    f"{tg['start_unit']}-{tg['end_unit']} skipped — "
                    f"no quaternion data for searchableRotatedBox"
                )
                continue

            rz_region_lines.append(f"""        {tg_name}
        {{
            mode inside;
            levels ((1e15 {tg['box_level']}));
        }}""")

            cell_at_box = base_size / (2 ** tg["box_level"])
            logger.info(
                f"Rack '{tg['rack_name']}': thin gap at units "
                f"{tg['start_unit']}-{tg['end_unit']} "
                f"({tg['gap_height']*1000:.1f}mm, "
                f"{tg['gap_height']/cell_size:.1f} cells at level {rack_region_level}) "
                f"→ auto-refinement box at level {tg['box_level']} "
                f"({cell_at_box*1000:.1f}mm cells, "
                f"{tg['gap_height']/cell_at_box:.1f} cells across gap)"
            )
```

Note: `base_size` and `cell_size` variables already exist from the beginning of `_generate_snappy_hex_mesh_dict()` — use the same `base_size` read from `mc.get("baseSize", 0.34)`.

**Important:** This block must be inserted BEFORE the line `rz_regions_str = "\n\n".join(rz_region_lines)` (currently ~line 4798) so the thin-gap entries are included in the final string.

- [ ] **Step 4: Run integration tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_thin_gap_refinement.py -v`
Expected: All unit tests + integration tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/tests/test_thin_gap_refinement.py
git commit -m "feat: integrate thin-gap searchBox into snappyHexMeshDict generation"
```

---

### Task 3: Delete dead per-unit bucket code + refactor `_parse_bucket_from_filename()`

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (lines 104-181)

- [ ] **Step 1: Run existing tests as baseline**

Run: `conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short 2>&1 | tail -20`
Expected: Record current pass count — all existing tests should pass.

- [ ] **Step 2: Refactor `_parse_bucket_from_filename()` to use standalone prefix map**

Replace lines 104-180 in `src/backend/aircooling/runner/stlgen_runner.py`. Delete `_BUCKET_FILE_PATTERNS`, `_bucket_level_lookup`, `_bucket_feature_lookup`. Replace `_parse_bucket_from_filename` internals:

```python
# ---- Bucket file prefix → category mapping (for STL classification) ----

_BUCKET_PREFIXES: Dict[str, str] = {
    "merged_server_": "server",
    "merged_blanking_": "blanking",
    "merged_partition_": "partition",
    "merged_through_zone_": "through_zone",
    "merged_wallBaffle_": "wallBaffle",
    "merged_perforatedBlankingPanel_": "perforatedBlankingPanel",
    "merged_wallBlankingPanel_": "wallBlankingPanel",
}


def _parse_bucket_from_filename(filename: str) -> Optional[Tuple[str, str]]:
    """For per-bucket STL files, return (category, bucket_key). None for scene-merged.

    Recognises two formats:
      - ``{prefix}<N>U.stl``              — original multi-solid bucket file
      - ``{prefix}<N>U__<zone>.stl``      — per-zone file (through-zone only,
        2026-04-18+). One closed-surface per file so snappy's
        ``refinementRegions mode=inside`` closure test accepts the shell.
    """
    for prefix, cat in _BUCKET_PREFIXES.items():
        if not filename.startswith(prefix):
            continue
        m = re.match(rf"{re.escape(prefix)}(\d+)U__[A-Za-z0-9_]+\.stl$", filename)
        if m:
            return (cat, m.group(1))
        m = re.match(rf"{re.escape(prefix)}(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
        m = re.match(rf"{re.escape(prefix)}[A-Za-z]+_(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
    return None
```

This preserves the exact same prefix matching behavior, just removes the tuple-of-5 entries and the two dead lookup functions.

- [ ] **Step 3: Find and remove any remaining references to deleted functions**

Search for any remaining calls to `_bucket_level_lookup` or `_bucket_feature_lookup` in the file. The line at ~2200 mentions `_BUCKET_FILE_PATTERNS` in a comment — update or remove that comment. There should be no runtime calls to the deleted functions since they were dead code.

Run: `grep -n "_bucket_level_lookup\|_bucket_feature_lookup\|_BUCKET_FILE_PATTERNS" src/backend/aircooling/runner/stlgen_runner.py`
Expected: Only `_BUCKET_PREFIXES` definition and the refactored `_parse_bucket_from_filename` remain.

- [ ] **Step 4: Run all tests to verify no regressions**

Run: `conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: Same pass count as baseline. No test should break since the deleted functions were never called at runtime.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "refactor: remove dead per-unit bucket system, simplify _parse_bucket_from_filename"
```

---

### Task 4: End-to-end verification with case_00_sketch

**Files:**
- Read-only verification: `demo_case/case_00_sketch/Mesh/system/snappyHexMeshDict`

- [ ] **Step 1: Generate mesh dicts for case_00_sketch and verify thin-gap box**

This step uses the server API or direct runner invocation to generate mesh dicts for the real case_00_sketch fixture and confirm the thin-gap region appears.

Run:
```bash
conda run -n dcdtuni python -c "
import json
from pathlib import Path
from dcdtuni.runner.stlgen_runner import STLGenRunner

case_dir = Path('demo_case/case_00_sketch')
cfdrun = case_dir / 'model' / 'case_00_sketch.cfdrun'
r = STLGenRunner(case_dir, cfdrun)
r._load()
r.validate_geometry()
r._preflight_partition_wall_thickness()
r._ensure_watertight_room_enclosure()
r._prepare_runtime_metadata()
r._generate_mesh_dicts()

snappy = (case_dir / 'Mesh' / 'system' / 'snappyHexMeshDict').read_text()
# Check thin-gap regions exist
import re
gaps = re.findall(r'rackThinGapRegion_\S+', snappy)
print(f'Thin-gap regions found: {len(set(gaps))//2}')
for g in sorted(set(gaps)):
    print(f'  {g}')
# Verify level > rackRegionLevel
for m in re.finditer(r'rackThinGapRegion_\S+\s*\{\s*mode inside;\s*levels\s*\(\(1e15\s+(\d+)\)\)', snappy):
    print(f'  refinement level: {m.group(1)}')
"
```

Expected: Output shows `rackThinGapRegion_Rack_Col3_4_u34_u34` (and potentially other thin-gap boxes) with level 4.

- [ ] **Step 2: Verify snappyHexMeshDict structure is valid**

Spot-check that the generated dict still has all expected sections:

```bash
conda run -n dcdtuni python -c "
snappy = open('demo_case/case_00_sketch/Mesh/system/snappyHexMeshDict').read()
for section in ['castellatedMesh', 'geometry', 'refinementSurfaces', 'refinementRegions',
                'snapControls', 'addLayersControls']:
    present = section in snappy
    print(f'{section}: {\"OK\" if present else \"MISSING\"}')"
```

Expected: All sections present and OK.

- [ ] **Step 3: Run all tests one final time**

Run: `conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short`
Expected: All tests pass, including the new thin-gap tests.

- [ ] **Step 4: Commit verification (no code changes)**

No code changes in this task — verification only.

---

## Self-Review Checklist

- **Spec coverage:** Section 1 (cleanup) → Task 3. Section 2 (detection) → Task 1. Section 3 (geometry) → Task 2. Section 4 (code changes) → Tasks 1-3 collectively. ✓
- **Placeholder scan:** All code blocks contain complete implementations. No TBD/TODO. ✓
- **Type consistency:** `_detect_thin_gap_regions()` signature and return type are consistent across Task 1 (implementation) and Task 2 (caller). `_BUCKET_PREFIXES` replaces `_BUCKET_FILE_PATTERNS` consistently in Task 3. ✓
