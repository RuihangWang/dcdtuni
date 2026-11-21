"""Tests for thin-gap auto-refinement detection algorithm."""
import copy
import json
import math
import re
import tempfile
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner, _detect_thin_gap_regions
from backend.tests._base_leakage_case import CFDRUN, make_case


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
        rack = _make_rack([
            _make_server(29, 5),
            _make_server(35, 1),
        ])
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 1
        g = gaps[0]
        assert g["rack_name"] == "Rack_A"
        assert g["start_unit"] == 34
        assert g["end_unit"] == 34
        assert g["box_level"] == 4

    def test_large_gap_does_not_trigger(self):
        rack = _make_rack([_make_server(10, 5)])
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 0

    def test_fully_sealed_rack_skipped(self):
        rack = _make_rack(
            [_make_server(29, 5), _make_server(35, 1)],
            blanking_type="fullBlock",
        )
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 0

    def test_fully_open_rack_refined_like_small_gap(self):
        """fullyOpen panels are the same cyclic baffles as typicalSmallGap (σ 1), so their gaps are refined too."""
        servers = [_make_server(29, 5), _make_server(35, 1)]
        open_gaps = _detect_thin_gap_regions(
            {"Rack_A": _make_rack(servers, blanking_type="fullyOpen")}, base_size=0.31, rack_region_level=3
        )
        small_gaps = _detect_thin_gap_regions(
            {"Rack_A": _make_rack(servers)}, base_size=0.31, rack_region_level=3
        )
        assert open_gaps == small_gaps and len(open_gaps) == 1

    def test_multiple_thin_gaps_each_gets_box(self):
        rack = _make_rack([
            _make_server(10, 1),
            _make_server(12, 1),
        ])
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        thin = [g for g in gaps if g["start_unit"] == 11]
        assert len(thin) == 1
        assert thin[0]["end_unit"] == 11

    def test_no_servers_no_gap(self):
        rack = _make_rack([])
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        assert len(gaps) == 0

    def test_safe_level_formula(self):
        rack = _make_rack([
            _make_server(20, 1),
            _make_server(22, 1),
        ], unit_height=0.04889)
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=3
        )
        thin = [g for g in gaps if g["start_unit"] == 21]
        assert len(thin) == 1
        assert thin[0]["box_level"] == 4

    def test_safe_level_with_low_rack_level(self):
        rack = _make_rack([
            _make_server(20, 1),
            _make_server(22, 1),
        ], unit_height=0.04889)
        gaps = _detect_thin_gap_regions(
            {"Rack_A": rack}, base_size=0.31, rack_region_level=1
        )
        thin = [g for g in gaps if g["start_unit"] == 21]
        assert len(thin) == 1
        assert thin[0]["box_level"] == 4

    def test_rack_cell_min_size_caps_the_gap_box(self):
        # Base604: 47.8 mm slots, base 0.25, rack region L2 (62.5 mm). Uncapped
        # the one-slot gap asks for L4 (15.6 mm); rackCellMinSize 17.5 mm caps it at L3.
        rack = _make_rack([_make_server(20, 4), _make_server(25, 4)], unit_height=0.04783)
        uncapped = _detect_thin_gap_regions({"R": rack}, base_size=0.25, rack_region_level=2)
        capped = _detect_thin_gap_regions({"R": rack}, base_size=0.25, rack_region_level=2,
                                          min_cell_size=0.0175)
        assert [g["box_level"] for g in uncapped] == [4]
        assert [g["box_level"] for g in capped] == [3]

    def test_no_box_when_the_cap_is_the_rack_level(self):
        # Rack region already at L3 (31.25 mm): the capped box would be L3 too, so none.
        rack = _make_rack([_make_server(20, 4), _make_server(25, 4)], unit_height=0.04783)
        assert _detect_thin_gap_regions({"R": rack}, base_size=0.25, rack_region_level=3,
                                        min_cell_size=0.0175) == []

    def test_gap_geometry_fields(self):
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

    def _generate_snappy(self, extra_servers=None, rack_region_level=3,
                         base_size=None):
        with open(CFDRUN) as f:
            original = json.load(f)
        data = copy.deepcopy(original)
        # V2 manifest: the runner's racks and meshConfig live in meshGeometry.
        mg = data["meshGeometry"]
        first_rack_name = next(iter(mg["racks"]))
        rack = mg["racks"][first_rack_name]
        if extra_servers:
            rack["servers"] = extra_servers
        rack["blankingPanelType"] = "typicalSmallGap"
        mg.setdefault("meshConfig", {})["rackRegionLevel"] = rack_region_level
        if base_size is not None:
            mg["meshConfig"]["baseSize"] = base_size
        with tempfile.TemporaryDirectory() as tmp:
            case_dir, tmp_cfdrun = make_case(Path(tmp), data)
            r = STLGenRunner(case_dir, tmp_cfdrun)
            r._load()
            r.validate_geometry()
            r._preflight_partition_wall_thickness()
            r._ensure_watertight_room_enclosure()
            r._prepare_runtime_metadata()
            r._generate_mesh_dicts()
            snappy_path = case_dir / "Mesh" / "system" / "snappyHexMeshDict"
            return snappy_path.read_text()

    def test_thin_gap_box_appears_in_geometry(self):
        servers = [
            {"id": "s1", "type": "fanServer", "startSlot": 29, "spanSlots": 5,
             "faces": {"inlet": {"solid": "s1_in"}, "outlet": {"solid": "s1_out"},
                       "top": {"solid": "s1_top"}, "bottom": {"solid": "s1_bot"}}},
            {"id": "s2", "type": "fanServer", "startSlot": 35, "spanSlots": 1,
             "faces": {"inlet": {"solid": "s2_in"}, "outlet": {"solid": "s2_out"},
                       "top": {"solid": "s2_top"}, "bottom": {"solid": "s2_bot"}}},
        ]
        text = self._generate_snappy(extra_servers=servers, rack_region_level=3,
                                     base_size=0.31)
        assert "rackThinGapRegion_" in text
        # Accept either searchableRotatedBox (quaternion path) or
        # searchableBox (axis-aligned fallback for legacy cfdrun files).
        block = _extract_block(text, re.findall(r"rackThinGapRegion_\S+", text)[0])
        assert block is not None
        assert ("searchableRotatedBox" in block or "searchableBox" in block)

    def test_thin_gap_refinement_level(self):
        servers = [
            {"id": "s1", "type": "fanServer", "startSlot": 29, "spanSlots": 5,
             "faces": {"inlet": {"solid": "s1_in"}, "outlet": {"solid": "s1_out"},
                       "top": {"solid": "s1_top"}, "bottom": {"solid": "s1_bot"}}},
            {"id": "s2", "type": "fanServer", "startSlot": 35, "spanSlots": 1,
             "faces": {"inlet": {"solid": "s2_in"}, "outlet": {"solid": "s2_out"},
                       "top": {"solid": "s2_top"}, "bottom": {"solid": "s2_bot"}}},
        ]
        text = self._generate_snappy(extra_servers=servers, rack_region_level=3,
                                     base_size=0.31)
        rr_match = re.search(
            r"rackThinGapRegion_\S+\s*\{\s*mode inside;\s*levels\s*\(\(1e15\s+(\d+)\)\)",
            text
        )
        assert rr_match is not None
        level = int(rr_match.group(1))
        assert level > 3

    def test_no_thin_gap_for_large_gaps(self):
        text = self._generate_snappy()
        assert "rackThinGapRegion_" not in text
