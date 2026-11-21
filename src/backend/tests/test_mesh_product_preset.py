"""The mesh budget survives the strict React manifest and bounds the whole job."""
import pytest
from backend.aircooling.runner.cfdrun_schema import MeshConfig, CFDExportMeshConfig
from backend.aircooling.runner.derived_config import derive_mesh_config
from backend.aircooling.runner.stlgen_runner import STLGenRunner, _mesh_time_limit_seconds


def test_product_budget_survives_strict_manifest_and_derivation():
    manifest = MeshConfig(meshBaseSize=0.25, meshEngine="dcCart", meshTimeLimitSeconds=120, parallelMesh=True, meshProcess=8)
    derived = derive_mesh_config(manifest.model_dump(exclude_none=True), {}, block_mesh_bounds=None)
    assert derived["meshTimeLimitSeconds"] == 120
    assert CFDExportMeshConfig.model_validate(derived).meshTimeLimitSeconds == 120


@pytest.mark.parametrize("value, expected", [(None, 300), (0, 300), (-5, 300), (900, 300), (30, 30), ("junk", 300), (float("nan"), 300), (float("inf"), 300)])
def test_every_case_has_a_five_minute_ceiling(value, expected):
    assert _mesh_time_limit_seconds({"meshTimeLimitSeconds": value}, 300) == expected


def test_native_and_runner_carry_no_time_limit(tmp_path):
    # soft 5-minute target (owner decision 2026-10-20): timed, never enforced
    runner = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "p.cfdrun")
    runner._data = {"meshConfig": {"meshEngine": "dcCart"}}
    runner._bg_grid_planes = {axis: [0.0, 1.0] for axis in ("x", "y", "z")}
    runner._write_dcCart_mesh_dict(tmp_path)
    assert "timeLimitSeconds    0;" in (tmp_path / "dcCartMeshDict").read_text()
    runner._start_mesh_budget()
    assert runner._mesh_time_remaining() is None
