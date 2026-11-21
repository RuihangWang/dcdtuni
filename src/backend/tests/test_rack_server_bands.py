"""rackRegionMode: the rack-region level reaches only the servers' slot bands.

The whole rack box drops one level and each server band (one slot of margin
above and below, overlaps merged) keeps rackRegionLevel; "whole" restores the
previous single box at the level.
"""
import copy
import json
import re
import tempfile
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import STLGenRunner, _rack_server_bands
from backend.tests._base_leakage_case import CFDRUN, make_case


def _rack(servers, unit_h=0.05, bottom=0.05):
    return {
        "geometry": {"unitHeight": unit_h},
        "safeMarginBottom": bottom,
        "servers": [{"startSlot": s, "spanSlots": n} for s, n in servers],
    }


def test_bands_follow_the_export_slot_arithmetic():
    # a server at slot 4 spanning 4 sits at 0.05 + 4 x 0.05 = 0.25 .. 0.45;
    # with one slot of margin: 0.20 .. 0.50
    assert _rack_server_bands(_rack([(4, 4)]), 2.2) == [(0.2, 0.5)]


def test_bands_merge_and_clip_to_the_rack():
    bands = _rack_server_bands(_rack([(0, 2), (3, 1), (42, 2)]), 2.2)
    # (0,2): -0.0 .. 0.20 -> clipped at 0; (3,1): 0.15 .. 0.30 overlaps it
    assert bands[0][0] == 0.0
    assert abs(bands[0][1] - 0.30) < 1e-12
    # (42,2): 2.10 .. 2.30 -> clipped at the rack height
    assert abs(bands[1][0] - 2.10) < 1e-12 and bands[1][1] == 2.2


def test_empty_rack_has_no_band():
    assert _rack_server_bands(_rack([]), 2.2) == []


def _levels(text):
    """name -> level of every refinementRegions entry."""
    out = {}
    block = text[text.index("refinementRegions"):]
    for name, lvl in re.findall(r"(\w+)\s*\{\s*mode inside;\s*levels \(\(1e15 (\d+)\)\);", block):
        out[name] = int(lvl)
    return out


def _generate(mode=None, depth=None, outlet_offset=None):
    with open(CFDRUN) as f:
        data = json.load(f)
    data = copy.deepcopy(data)
    mc = data["meshGeometry"].setdefault("meshConfig", {})
    if mode is not None:
        mc["rackRegionMode"] = mode
    if depth is not None:
        mc["rackRegionDepth"] = depth
    if outlet_offset is not None:
        for rack in data["meshGeometry"]["racks"].values():
            for srv in rack.get("servers") or []:
                srv["outletOffsetM"] = outlet_offset
    with tempfile.TemporaryDirectory() as tmp:
        case_dir, cfdrun = make_case(Path(tmp), data)
        r = STLGenRunner(case_dir, cfdrun)
        r._load()
        r.validate_geometry()
        r._preflight_partition_wall_thickness()
        r._ensure_watertight_room_enclosure()
        r._prepare_runtime_metadata()
        r._generate_mesh_dicts()
        text = (case_dir / "Mesh" / "system" / "snappyHexMeshDict").read_text()
    return text, data["meshGeometry"]["racks"]


def test_servers_mode_refines_the_bands_and_coarsens_the_rest():
    text, racks = _generate()
    levels = _levels(text)
    rack_boxes = {k: v for k, v in levels.items() if re.fullmatch(r"rackRegion_\w+?", k) and "_srv" not in k}
    bands = {k: v for k, v in levels.items() if "_srv" in k}
    assert rack_boxes and bands
    top = max(bands.values())
    assert all(v == top - 1 for v in rack_boxes.values())
    # every rack holding a server has at least one band named after it
    for name, rack in racks.items():
        if rack.get("servers"):
            assert any(b.startswith(f"rackRegion_{name}_srv") for b in bands), name


def test_whole_mode_is_the_previous_single_box():
    text, _ = _generate("whole")
    levels = _levels(text)
    assert not any("_srv" in k for k in levels)
    rack_boxes = [v for k, v in levels.items() if k.startswith("rackRegion_")]
    assert rack_boxes and len(set(rack_boxes)) == 1


def test_clearance_experiment_refines_side_gaps_and_front_plenum(monkeypatch):
    # DCDTUNI_RACK_CLEARANCE_LEVEL (opt-in): three boxes per rack, one level
    # finer than asked, spanning the full rack height; off by default.
    monkeypatch.delenv("DCDTUNI_RACK_CLEARANCE_LEVEL", raising=False)
    text, _ = _generate()
    assert "_sideA" not in text
    with open(CFDRUN) as f:
        level = json.load(f)["meshGeometry"]["meshConfig"].get("rackRegionLevel", 2)
    monkeypatch.setenv("DCDTUNI_RACK_CLEARANCE_LEVEL", str(level + 1))
    text, racks = _generate()
    levels = _levels(text)
    for part in ("sideA", "sideB", "front"):
        boxes = {k: v for k, v in levels.items() if k.endswith("_" + part)}
        assert len(boxes) == len(racks)
        assert set(boxes.values()) == {level + 1}
    rack = next(iter(racks.values()))["geometry"]
    m = re.search(r"rackRegion_\w+_sideA\s*\{[^}]*span \(([\d.]+) ([\d.]+) ([\d.]+)\)", text)
    side, height, depth = map(float, m.groups())
    assert abs(height - rack["size"]["H"]) < 1e-6 and abs(depth - rack["size"]["D"]) < 1e-6
    assert side < rack["size"]["W"] / 2


def _band_depths(text):
    """name -> depth span (third span component) of every server band box."""
    return {name: float(d) for name, d in re.findall(
        r"(rackRegion_\w+_srv\d+)\s*\{[^}]*span \([\d.]+ [\d.]+ ([\d.]+)\)", text)}


def test_full_depth_is_the_default():
    text, racks = _generate(outlet_offset=0.3)
    depths = _band_depths(text)
    D = next(iter(racks.values()))["geometry"]["size"]["D"]
    assert depths and all(abs(d - D) < 1e-6 for d in depths.values())


def test_slots_depth_stops_the_bands_one_cell_past_the_outlet_plane():
    # rackRegionDepth "slots": the rear plenum behind the servers' outlet plane
    # stays at the rack box's coarser level, but for one rack-level cell
    with open(CFDRUN) as f:
        mc = json.load(f)["meshGeometry"]["meshConfig"]
    text, racks = _generate(depth="slots", outlet_offset=0.3)
    levels = _levels(text)
    level = max(v for k, v in levels.items() if "_srv" in k)
    cell = float(mc.get("baseSize", 0.25) or 0.25) / 2 ** level
    depths = _band_depths(text)
    D = next(iter(racks.values()))["geometry"]["size"]["D"]
    assert depths
    for d in depths.values():
        assert abs(d - min(D, D - 0.3 + cell)) < 1e-6, (d, D, cell)


def test_unknown_depth_is_refused():
    import pytest
    with pytest.raises(ValueError, match="rackRegionDepth"):
        _generate(depth="half")


def test_project_choice_travels_through_the_derived_mesh_config():
    from backend.aircooling.runner.derived_config import derive_mesh_config
    plain = derive_mesh_config({}, {}, block_mesh_bounds=None)
    assert "rackRegionMode" not in plain and "rackRegionDepth" not in plain
    chosen = derive_mesh_config({"rackRegionMode": "servers", "rackRegionDepth": "slots"}, {}, block_mesh_bounds=None)
    assert chosen["rackRegionMode"] == "servers" and chosen["rackRegionDepth"] == "slots"


def test_the_v2_manifest_admits_the_rack_region_shape():
    import pytest
    from pydantic import ValidationError
    from backend.aircooling.runner.cfdrun_schema import MeshConfig
    assert MeshConfig(rackRegionMode="whole", rackRegionDepth="slots").rackRegionDepth == "slots"
    with pytest.raises(ValidationError):
        MeshConfig(rackRegionDepth="half")
