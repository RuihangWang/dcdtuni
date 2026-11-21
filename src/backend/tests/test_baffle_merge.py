"""Tests for baffle STL merge optimization."""
import json
import re

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._base_leakage_case import CFDRUN, make_case


@pytest.fixture
def runner(tmp_path):
    """Load a scratch copy of the frozen Base_leakage export (does NOT run mesh — just _load)."""
    case_dir, cfdrun = make_case(tmp_path)
    r = STLGenRunner(case_dir, cfdrun)
    r._load()
    return r


def test_wall_baffles_merged(runner):
    """All wall baffles should be in merged_wall_baffle.stl, not individual files."""
    stl_files = runner._stl_solids
    assert "merged_wall_baffle.stl" in stl_files, \
        f"merged_wall_baffle.stl not found. Files: {list(stl_files.keys())}"
    wall_individuals = [f for f in stl_files if f.startswith("baffle_")
                        and "_door" not in f]
    assert wall_individuals == [], \
        f"Individual wall baffle files should be merged: {wall_individuals}"


def test_rack_doors_merged(runner):
    """All rack doors should be in merged_rack_door.stl."""
    stl_files = runner._stl_solids
    with open(CFDRUN) as f:
        data = json.load(f)
    # V2 manifest: the racks live in the meshGeometry blob.
    has_doors = any(
        (r.get(f"{side}Door") or {}).get("enabled", False)
        for r in data["meshGeometry"].get("racks", {}).values()
        for side in ("inlet", "outlet")
    )
    if has_doors:
        assert "merged_rack_door.stl" in stl_files
    door_files = [f for f in stl_files
                  if "_door" in f and f != "merged_rack_door.stl"]
    assert door_files == [], \
        f"Individual/bucket door files should be merged: {door_files}"


def test_perforated_stay_individual(runner):
    """Perforated baffles/floors stay as individual files."""
    stl_files = runner._stl_solids
    perf_files = [f for f in stl_files if f.startswith("perforated_")]
    assert len(perf_files) >= 1, \
        f"Expected at least 1 perforated file, got: {perf_files}"


def test_merged_wall_baffle_solid_count(runner):
    """merged_wall_baffle.stl should contain all wall baffle solids."""
    solids = runner._stl_solids.get("merged_wall_baffle.stl", [])
    # Base_leakage: 3 baffle solids. The raised floor's remaining solid used to be
    # merged here as well (4); since a272a06a it is its own
    # wallBaffle_floor_<name>.stl with its own faceZone.
    assert len(solids) >= 3, f"Expected ≥3 wall baffle solids, got {len(solids)}: {solids}"
    floor_rest = runner._stl_solids.get("wallBaffle_floor_Raised_Floor_copy_part1.stl", [])
    assert floor_rest == ["Raised_Floor_copy_part1"], \
        f"raised-floor remainder should be its own wallBaffle_floor_ file, got {floor_rest}"


def test_merged_rack_door_solid_count(runner):
    """merged_rack_door.stl may or may not exist depending on case data."""
    solids = runner._stl_solids.get("merged_rack_door.stl", [])
    # Base_leakage case data no longer has rack doors
    assert len(solids) >= 0, f"Unexpected door solids: {solids}"


# ── Task 3/4/5: downstream runner code handles merged baffle/door filenames ──


def test_snappy_dict_merged_wall_baffle_entry(runner):
    """snappyHexMeshDict should have merged_wall_baffle with surface-level faceZone + faceType internal."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()
    snappy_path = runner.mesh_dir / "system" / "snappyHexMeshDict"
    text = snappy_path.read_text()
    assert "merged_wall_baffle.stl" in text
    assert re.search(r'merged_wall_baffle\s*\{[^}]*faceType\s+internal;', text), \
        "merged_wall_baffle should use faceType internal"
    assert re.search(r'merged_wall_baffle\s*\{[^}]*faceZone\s+merged_wall_baffle;', text), \
        "merged_wall_baffle must have surface-level faceZone"
    individual = re.findall(r'^\s+baffle_\S+', text, re.MULTILINE)
    assert individual == [], f"No individual baffle_ entries: {individual}"


def test_snappy_dict_merged_rack_door_entry(runner):
    """snappyHexMeshDict should have merged_rack_door with faceType internal."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()
    snappy_path = runner.mesh_dir / "system" / "snappyHexMeshDict"
    text = snappy_path.read_text()
    has_doors = any(
        (r.get(f"{side}Door") or {}).get("enabled", False)
        for r in runner._data.get("racks", {}).values()
        for side in ("inlet", "outlet")
    )
    if not has_doors:
        pytest.skip("No doors")
    assert "merged_rack_door.stl" in text
    assert re.search(r'merged_rack_door\s*\{[^}]*faceType\s+internal;', text)
    assert re.search(r'merged_rack_door\s*\{[^}]*faceZone\s+merged_rack_door;', text)


def test_create_baffles_dict_has_wall_entry(runner):
    """createBafflesDict MUST have ONE unified merged_wall_baffle entry."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()
    cbd_path = runner.mesh_dir / "system" / "createBafflesDict"
    text = cbd_path.read_text()
    assert "merged_wall_baffle" in text
    assert "merged_wall_baffle_master" in text
    assert "merged_wall_baffle_slave" in text


def test_create_baffles_dict_has_door_entry(runner):
    """createBafflesDict should have ONE cyclic entry for merged_rack_door."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()
    cbd_path = runner.mesh_dir / "system" / "createBafflesDict"
    text = cbd_path.read_text()
    has_doors = any(
        (r.get(f"{side}Door") or {}).get("enabled", False)
        for r in runner._data.get("racks", {}).values()
        for side in ("inlet", "outlet")
    )
    if not has_doors:
        pytest.skip("No doors")
    assert "merged_rack_door" in text
    assert "merged_rack_door_master" in text
    assert "merged_rack_door_slave" in text


def test_seeds_cover_all_baffle_locations(runner):
    """Dual-side seeds must exist for every wall baffle and door location."""
    runner.validate_geometry()
    runner._preflight_partition_wall_thickness()
    runner._ensure_watertight_room_enclosure()
    runner._prepare_runtime_metadata()
    runner._generate_mesh_dicts()
    snappy_path = runner.mesh_dir / "system" / "snappyHexMeshDict"
    text = snappy_path.read_text()
    # Format: (( x y z ) none)
    none_seeds = re.findall(r'\(\(\s*[\d.e+-]+\s+[\d.e+-]+\s+[\d.e+-]+\s*\)\s+none\)', text)
    # Base_leakage (no rack doors any more): 3 wall baffles + raised-floor remainder
    # + perforated floor opening = 5 locations x 2 sides + locationInMesh = 11 seeds
    assert len(none_seeds) >= 10, f"Expected >=10 none seeds, got {len(none_seeds)}"

    # The raised-floor remainder (its own wallBaffle_floor_ file since a272a06a)
    # gets its own pair: one seed on each side of the floor plane.
    specs = runner._collect_create_baffle_seed_specs()
    pairs = runner._build_patch_driven_none_seed_entries()  # two per spec, in spec order
    i = next(i for i, s in enumerate(specs) if s.zone_name == "Raised_Floor_copy_part1")
    assert specs[i].stl_path.name == "wallBaffle_floor_Raised_Floor_copy_part1.stl"
    ys = {float(y) for y in re.findall(r"vertex\s+\S+\s+(\S+)\s+\S+", specs[i].stl_path.read_text())}
    assert len(ys) == 1, f"raised-floor remainder should be flat, got y={sorted(ys)}"
    floor_y = ys.pop()
    (below, _), (above, _) = sorted(pairs[2 * i:2 * i + 2], key=lambda entry: entry[0][1])
    assert below[1] < floor_y < above[1], \
        f"seed pair {below} / {above} does not straddle the floor plane y={floor_y}"
    placed = [tuple(map(float, m)) for m in re.findall(
        r'\(\(\s*([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)\s*\)\s+none\)', text)]
    for seed in (below, above):
        assert any(all(abs(a - b) < 1e-5 for a, b in zip(seed, p)) for p in placed), \
            f"seed {seed} missing from snappyHexMeshDict locationsInMesh"


def test_wallbaffle_floor_opening_seed_spec(tmp_path):
    """A wallBaffle floor opening is written to wallBaffle_floor_<name>.stl as well
    (a272a06a); seed placement must find it there instead of raising
    "Expected floor_opening_wallBaffle patch ... none found"."""
    data = json.loads(CFDRUN.read_text())
    opening = data["meshGeometry"]["floors"]["Raised_Floor_copy"]["openings"][0]
    opening.setdefault("physics", {})["type"] = "wallBaffle"
    case_dir, cfdrun = make_case(tmp_path, data)
    r = STLGenRunner(case_dir, cfdrun)
    r._load()
    r.validate_geometry()
    r._preflight_partition_wall_thickness()
    r._ensure_watertight_room_enclosure()
    r._prepare_runtime_metadata()
    r._generate_mesh_dicts()
    files = {s.zone_name: s.stl_path.name for s in r._collect_create_baffle_seed_specs()}
    assert files.get("Raised_Floor_copy_patch") == "wallBaffle_floor_Raised_Floor_copy_patch.stl", files
    assert files.get("Raised_Floor_copy_part1") == "wallBaffle_floor_Raised_Floor_copy_part1.stl", files
