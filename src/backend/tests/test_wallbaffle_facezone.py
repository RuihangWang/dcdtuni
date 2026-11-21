"""Regression: wall-baffle solids must go through createBaffles to get
proper master/slave patches, not snappy's faceType baffle (single-sided).

Bug (pre-fix): per-region faceZone inside regions{} is ignored by
snappyHexMesh — faces become boundary patches instead of internal faces.
createBaffles finds empty faceZones and produces 0-face master/slave.

Fix: use surface-level faceZone + faceType internal (same pattern as
perforated floors and merged_rack_door). createBaffles creates ONE
merged_wall_baffle_master/_slave cyclic pair, then
_convert_wall_baffle_patches_to_wall() converts them to wall type.
"""
import re
from pathlib import Path

from .test_refinement_redesign import _bootstrap_runner, _write_dummy_stl


def _make_wallbaffle_runner(tmp_path):
    stl_solids = {
        "merged_wall_baffle.stl": ["Raising_floor_part1", "Baffle_1", "Baffle_2"],
        "merged_box.stl": ["Box_1"],
    }
    mesh_config = {"baseSize": 0.35}
    baffles = {
        "Baffle_1": {"stlFile": "merged_baffle.stl", "geometry": {}, "physics": {"baffleType": "wall"}},
        "Baffle_2": {"stlFile": "merged_baffle.stl", "geometry": {}, "physics": {"baffleType": "wall"}},
    }
    floors = {
        "Raising_floor": {
            "stlFile": "merged_floor_ceiling.stl",
            "geometry": {"anchor": {"x": 0, "y": 0.4, "z": 0}, "size": {"W": 10, "D": 7}},
            "defaultBC": "wallBaffle",
            "openings": [],
        },
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)

    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    runner._data["baffles"] = baffles
    runner._data["floors"] = floors
    return runner


def test_surface_level_facezone_on_merged_wall_baffle(tmp_path):
    """merged_wall_baffle MUST have a surface-level faceZone directive.
    Per-region faceZone does not work in snappyHexMesh — only
    surface-level faceZone + faceType internal keeps faces internal."""
    runner = _make_wallbaffle_runner(tmp_path)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    pattern = re.search(
        r'merged_wall_baffle\s*\{[^}]*faceZone\s+merged_wall_baffle\s*;',
        text, re.DOTALL
    )
    assert pattern, \
        "merged_wall_baffle must have surface-level faceZone merged_wall_baffle"


def test_no_per_region_facezone(tmp_path):
    """Individual regions must NOT have faceZone — only refinement levels.
    Per-region faceZone is ignored by snappyHexMesh."""
    runner = _make_wallbaffle_runner(tmp_path)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    for solid in ["Raising_floor_part1", "Baffle_1", "Baffle_2"]:
        region_block = re.search(
            rf'{solid}\s*\{{([^}}]*)\}}', text
        )
        assert region_block, f"Region {solid} not found in snappyHexMeshDict"
        region_content = region_block.group(1)
        assert "faceZone" not in region_content, \
            f"Region {solid} must NOT have faceZone (doesn't work in snappy)"
        assert "faceType" not in region_content, \
            f"Region {solid} must NOT have faceType (only level)"


def test_wall_baffle_uses_facetype_internal(tmp_path):
    """Surface-level faceType must be internal (not baffle).
    faceType internal keeps faces internal so createBaffles can
    convert them to proper master/slave patches."""
    runner = _make_wallbaffle_runner(tmp_path)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    pattern = re.search(
        r'merged_wall_baffle\s*\{[^}]*faceType\s+internal\s*;',
        text, re.DOTALL
    )
    assert pattern, \
        "merged_wall_baffle must use surface-level faceType internal"


def test_createbaffles_uses_merged_zone(tmp_path):
    """createBafflesDict must have ONE entry for merged_wall_baffle zone,
    not per-solid entries. Creates merged_wall_baffle_master/_slave."""
    runner = _make_wallbaffle_runner(tmp_path)
    system_dir = tmp_path / "Mesh" / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    runner._generate_create_baffles_dict()
    cb_path = tmp_path / "Mesh" / "system" / "createBafflesDict"
    assert cb_path.exists(), "createBafflesDict must be generated"
    text = cb_path.read_text()
    assert "merged_wall_baffle_master" in text, \
        "merged_wall_baffle_master must be in createBafflesDict"
    assert "merged_wall_baffle_slave" in text, \
        "merged_wall_baffle_slave must be in createBafflesDict"
    assert "zoneName    merged_wall_baffle;" in text, \
        "zoneName must be merged_wall_baffle (not per-solid)"
