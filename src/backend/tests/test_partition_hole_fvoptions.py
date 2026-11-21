"""Tests for partition-hole cellZones and Darcy-Forchheimer fvOptions config."""

import json
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry


def _write_ascii_stl(path: Path, solid_name: str) -> None:
    path.write_text(
        f"""solid {solid_name}
facet normal 0 0 1
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 1 0
  endloop
endfacet
endsolid {solid_name}
""",
        encoding="utf-8",
    )


def _make_cfd_export() -> dict:
    return {
        "version": "1.0",
        "source": "frontend",
        "projectName": "test",
        "room": {
            "height": 3.0,
            "plane": [
                {"x": 0, "y": 0, "z": 0},
                {"x": 10, "y": 0, "z": 0},
                {"x": 10, "y": 10, "z": 0},
                {"x": 0, "y": 10, "z": 0},
            ],
            "locationInMesh": {"x": 5, "y": 5, "z": 1.5},
        },
        "meshConfig": {
            "refinementLevel": [1, 1],
            "leakageRefinementLevel": [2, 3],
        },
        "partitionWalls": {
            "Partition_Wall_1": {
                "stlFile": "partition_wall_1.stl",
                "geometry": {
                    "anchor": {"x": 0, "y": 0, "z": 0},
                    "size": {"W": 1.0, "D": 0.2, "H": 2.0},
                    "transformQuaternion": {"x": 0, "y": 0, "z": 0, "w": 1},
                },
                "holes": [
                    {
                        "patchId": "hole_1",
                        "zoneName": "Partition_Wall_1_hole_1",
                        "zoneStlFile": "partition_hole_1.stl",
                        "physics": {"resistanceEnabled": True, "leakageRate": 0.0},
                    },
                    {
                        "patchId": "hole_2",
                        "zoneName": "Partition_Wall_1_hole_2",
                        "zoneStlFile": "partition_hole_2.stl",
                        "physics": {"resistanceEnabled": False, "leakageRate": 25.0},
                    },
                    {
                        "patchId": "hole_3",
                        "zoneName": "Partition_Wall_1_hole_3",
                        "zoneStlFile": "partition_hole_3.stl",
                        "physics": {"resistanceEnabled": True, "leakageRate": 100.0},
                    },
                ],
            }
        },
    }


def _setup_runner(tmp_path: Path) -> STLGenRunner:
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(_make_cfd_export())), encoding="utf-8")

    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")
    _write_ascii_stl(tri_surface_dir / "partition_wall_1.stl", "Partition_Wall_1_body")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", "Partition_Wall_1_hole_1")
    _write_ascii_stl(tri_surface_dir / "partition_hole_2.stl", "Partition_Wall_1_hole_2")
    _write_ascii_stl(tri_surface_dir / "partition_hole_3.stl", "Partition_Wall_1_hole_3")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    runner._load()
    runner._build_partition_wall_seed_entries = lambda: []
    return runner


def _setup_runner_with_partition_helper_solid(
    tmp_path: Path,
    helper_solid_name: str,
    depth: float = 0.2,
    leakage_rate: float = 0.0,
) -> STLGenRunner:
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    data["partitionWalls"]["Partition_Wall_1"]["geometry"]["size"]["D"] = depth
    data["partitionWalls"]["Partition_Wall_1"]["holes"] = [
        {
            "patchId": "hole_1",
            "zoneName": "Partition_Wall_1_hole_1",
            "zoneStlFile": "partition_hole_1.stl",
            "physics": {"resistanceEnabled": True, "leakageRate": leakage_rate},
        }
    ]
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")

    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")
    _write_ascii_stl(tri_surface_dir / "partition_wall_1.stl", "Partition_Wall_1_body")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", helper_solid_name)

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    runner._load()
    runner._build_partition_wall_seed_entries = lambda: []
    return runner


def test_load_discovers_partition_hole_stls(tmp_path):
    """Holes with leakageRate < 100 are kept; holes with leakageRate >= 100 are skipped."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(_make_cfd_export())), encoding="utf-8")
    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", "Partition_Wall_1_hole_1")
    # hole_2 has leakageRate=25 (< 100) → included regardless of resistanceEnabled
    _write_ascii_stl(tri_surface_dir / "partition_hole_2.stl", "Partition_Wall_1_hole_2")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    runner._load()

    assert "merged_box.stl" in runner._stl_solids
    assert "partition_hole_1.stl" in runner._stl_solids
    # leakageRate=25 < 100 → hole_2 IS included (resistanceEnabled is legacy, ignored)
    assert "partition_hole_2.stl" in runner._stl_solids


def test_load_fails_when_referenced_partition_helper_stl_is_missing(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    data["partitionWalls"]["Partition_Wall_1"]["holes"] = [
        {
            "patchId": "hole_1",
            "zoneName": "Partition_Wall_1_hole_1",
            "zoneStlFile": "partition_hole_1.stl",
            "physics": {"resistanceEnabled": True, "leakageRate": 0.0},
        }
    ]
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")
    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    with pytest.raises(RuntimeError, match=r"partition_hole_1\.stl.*missing"):
        runner._load()


def test_load_fails_when_only_partition_helpers_exist(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    data["partitionWalls"]["Partition_Wall_1"]["holes"] = [
        {
            "patchId": "hole_1",
            "zoneName": "Partition_Wall_1_hole_1",
            "zoneStlFile": "partition_hole_1.stl",
            "physics": {"resistanceEnabled": True, "leakageRate": 0.0},
        }
    ]
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", "Partition_Wall_1_hole_1")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    with pytest.raises(FileNotFoundError, match=r"No grouped STL files"):
        runner._load()


def test_load_fails_when_referenced_partition_helper_stl_is_empty(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    data["partitionWalls"]["Partition_Wall_1"]["holes"] = [
        {
            "patchId": "hole_1",
            "zoneName": "Partition_Wall_1_hole_1",
            "zoneStlFile": "partition_hole_1.stl",
            "physics": {"resistanceEnabled": True, "leakageRate": 0.0},
        }
    ]
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")
    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")
    (tri_surface_dir / "partition_hole_1.stl").write_text("", encoding="utf-8")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    with pytest.raises(RuntimeError, match=r"partition_hole_1\.stl.*no solids"):
        runner._load()


def test_load_ignores_stale_unreferenced_partition_helper_stl(tmp_path):
    """Stale helper STLs are dropped from _stl_solids but kept on disk for safety."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    data["partitionWalls"]["Partition_Wall_1"]["holes"] = [
        {
            "patchId": "hole_1",
            "zoneName": "Partition_Wall_1_hole_1",
            "zoneStlFile": "partition_hole_1.stl",
            "physics": {"resistanceEnabled": True, "leakageRate": 0.0},
        }
    ]
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")

    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", "Partition_Wall_1_hole_1")
    _write_ascii_stl(tri_surface_dir / "partition_hole_stale.stl", "Stale_Helper")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    runner._load()

    assert "partition_hole_1.stl" in runner._stl_solids
    assert "partition_hole_stale.stl" not in runner._stl_solids
    # File is intentionally kept on disk for safety (solve-only may need it later)
    assert (tri_surface_dir / "partition_hole_stale.stl").exists()


def test_load_rejects_partition_helper_with_wrong_solid_name(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")

    _write_ascii_stl(tri_surface_dir / "merged_box.stl", "Merged_Box")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", "Wrong_Solid_Name")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    with pytest.raises(RuntimeError, match=r"partition_hole_1\.stl.*Wrong_Solid_Name.*Partition_Wall_1_hole_1"):
        runner._load()


def test_partition_hole_is_internal_cellzone_in_snappy_dict(tmp_path):
    runner = _setup_runner(tmp_path)
    system_dir = runner.case_dir / "Mesh" / "system"
    system_dir.mkdir(parents=True)

    runner._generate_snappy_hex_mesh_dict(system_dir)

    content = (system_dir / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert "partition_hole_1.stl" in content
    assert "faceType internal;" in content
    assert "cellZone partition_hole_1;" in content
    assert "cellZoneInside inside;" in content


def test_partition_helper_load_normalizes_surface_name_across_seed_and_fvoptions(tmp_path):
    runner = _setup_runner_with_partition_helper_solid(
        tmp_path,
        helper_solid_name="Partition Wall-1 hole 1",
        leakage_rate=0.0,
    )

    assert runner._stl_solids["partition_hole_1.stl"] == ["partition_hole_1"]

    seed_entries = runner._build_partition_hole_cellzone_seed_entries()
    assert seed_entries
    assert seed_entries[0][1] == "partition_hole_1"

    config = runner._build_partition_hole_fvoptions_config()
    assert "partition_hole_1" in config
    assert config["partition_hole_1"].cellzone_name == "partition_hole_1"

    system_dir = runner.case_dir / "Mesh" / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    runner._generate_snappy_hex_mesh_dict(system_dir)
    content = (system_dir / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert "partition_hole_1" in content
    assert "Partition_Wall_1_hole_1" not in content


def test_partition_helper_sanitized_solid_name_still_reaches_seed_and_fvoptions(tmp_path):
    runner = _setup_runner_with_partition_helper_solid(
        tmp_path,
        helper_solid_name="Partition Wall-1 hole-1",
        leakage_rate=25.0,
    )

    assert runner._stl_solids["partition_hole_1.stl"] == ["partition_hole_1"]

    seed_entries = runner._build_partition_hole_cellzone_seed_entries()
    assert seed_entries[0][1] == "partition_hole_1"
    assert seed_entries[0][0] == pytest.approx((1 / 3, 1 / 3, 0.0))

    config = runner._build_partition_hole_fvoptions_config()
    assert "partition_hole_1" in config
    assert config["partition_hole_1"].cellzone_name == "partition_hole_1"
    assert config["partition_hole_1"].sigma == pytest.approx(0.25)


def test_locations_in_mesh_include_only_enabled_partition_hole_seeds(tmp_path):
    """Holes with leakageRate < 100 get seeds; hole_3 (leakageRate=100) does not."""
    runner = _setup_runner(tmp_path)

    entries = runner._build_locations_in_mesh_entries()
    zone_names = [name for _, name in entries]

    assert "partition_hole_1" in zone_names
    # hole_2: leakageRate=25 < 100 → included (resistanceEnabled is legacy, ignored)
    assert "partition_hole_2" in zone_names
    # hole_3: leakageRate=100 → excluded
    assert "partition_hole_3" not in zone_names


def test_partition_hole_fvoptions_config_is_built_for_valid_holes(tmp_path):
    """fvOptions built for holes with leakageRate < 100; hole_3 excluded."""
    runner = _setup_runner(tmp_path)

    config = runner._build_partition_hole_fvoptions_config()

    assert "partition_hole_1" in config
    zone_cfg = config["partition_hole_1"]
    assert zone_cfg.cellzone_name == "partition_hole_1"
    assert zone_cfg.origin
    assert zone_cfg.e1
    assert zone_cfg.sigma == pytest.approx(0.0)

    # hole_2: leakageRate=25 < 100 → included with σ=0.25
    assert "partition_hole_2" in config
    assert config["partition_hole_2"].sigma == pytest.approx(0.25)

    # hole_3: leakageRate=100 → excluded
    assert "partition_hole_3" not in config


def test_partition_hole_fvoptions_depth_missing_uses_min_clamp(tmp_path, monkeypatch):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_surface_dir.mkdir(parents=True)

    data = _make_cfd_export()
    data["partitionWalls"]["Partition_Wall_1"]["geometry"]["size"].pop("D")
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(data)), encoding="utf-8")

    _write_ascii_stl(tri_surface_dir / "partition_wall_1.stl", "Partition_Wall_1_body")
    _write_ascii_stl(tri_surface_dir / "partition_hole_1.stl", "partition_hole_1")

    runner = STLGenRunner(case_dir, cfdrun_path=cfd_path)
    runner._load_cfd_json_only()
    runner._stl_solids = {
        "partition_wall_1.stl": ["Partition_Wall_1_body"],
        "partition_hole_1.stl": ["partition_hole_1"],
    }
    runner._build_partition_wall_seed_entries = lambda: []

    calls = []
    import backend.aircooling.runner.stlgen_runner as stlgen_runner_module

    def _spy_darcy_forchheimer(sigma, depth, min_depth=0.1):
        calls.append((sigma, depth, min_depth))
        return (0.0, 1.0)

    monkeypatch.setattr(stlgen_runner_module, "_darcy_forchheimer", _spy_darcy_forchheimer)
    config = runner._build_partition_hole_fvoptions_config()
    zone_cfg = config["partition_hole_1"]
    assert zone_cfg.sigma == pytest.approx(0.0)
    assert calls == [(0.0, 0.001, 0.001)]


def test_partition_hole_fvoptions_uses_wall_thickness_not_legacy_clamp(tmp_path):
    runner = _setup_runner_with_partition_helper_solid(
        tmp_path,
        helper_solid_name="Partition_Wall_1_hole_1",
        depth=0.02,
        leakage_rate=50.0,
    )

    config = runner._build_partition_hole_fvoptions_config()
    zone_cfg = config["partition_hole_1"]
    assert zone_cfg.sigma == pytest.approx(0.5)
    assert zone_cfg.f == "(272.222 0 0)"
