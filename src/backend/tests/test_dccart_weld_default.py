"""dcCartMeshDict carries the near-coincidence weld tolerance (DAML-Testbed, 2026-09-21).

dcCartMesh reads ``weldTolerance`` from system/dcCartMeshDict (0 = off) and
the runner used to write nothing, so a click in the web frontend meshed with
the weld OFF while the environment knob DCCART_WELD=1 (which the evidence runs
used) turned it on. With it OFF the DAML-Testbed steady solve collapses (k to
1e-7 by step 20, cumulative continuity past 7); at 1.2e-3 m every field decays
2-3 orders and the continuity error stays at 3e-4, on both DAML project
variants, while Tri_angle / Slab / Shinaide keep every gate decision. The
runner therefore writes the tolerance itself, from ``meshConfig.weldTolerance``
when the manifest carries one and 1.2e-3 m otherwise; 0 disables.
"""
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig, MeshConfig
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _dict_text(tmp_path: Path, mesh_config: dict) -> str:
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r._data = {"meshConfig": {"meshEngine": "dcCart", **mesh_config}}
    r._bg_grid_planes = {axis: [0.0, 1.0] for axis in ("x", "y", "z")}
    r._write_dcCart_mesh_dict(tmp_path)
    return (tmp_path / "dcCartMeshDict").read_text()


def test_default_weld_is_on_at_1p2mm(tmp_path):
    assert "weldTolerance       0.0012;" in _dict_text(tmp_path, {})


@pytest.mark.parametrize("value, expected", [
    (0, "0"), (0.0, "0"), (0.002, "0.002"), ("0.0005", "0.0005"), (-1, "0"),
])
def test_manifest_value_is_honoured(tmp_path, value, expected):
    assert f"weldTolerance       {expected};" in _dict_text(tmp_path, {"weldTolerance": value})


@pytest.mark.parametrize("value", [None, "junk", ""])
def test_unusable_value_falls_back_to_default(tmp_path, value):
    assert "weldTolerance       0.0012;" in _dict_text(tmp_path, {"weldTolerance": value})


def test_schema_declares_the_key():
    # extra="forbid" on the V2 MeshConfig: an undeclared key would reject every
    # manifest that carries it.
    assert MeshConfig(weldTolerance=0.002).weldTolerance == 0.002
    assert CFDExportMeshConfig(weldTolerance=0).weldTolerance == 0
    assert CFDExportMeshConfig().weldTolerance is None
