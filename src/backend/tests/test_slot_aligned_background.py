"""meshConfig.slotAlignedBackground: the vertical background on the racks' U-slot lattice.

Off (the default) the pitch is room height / round(height / meshBaseSize); on,
the planes are lattice origin + k x meshBaseSize exactly, so with
meshBaseSize = 1U x 2^rackRegionLevel every slot face lies on a cell plane.
"""
import copy
import json
import tempfile
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner, _rack_slot_lattice
from backend.tests._base_leakage_case import CFDRUN, make_case


def _planes(aligned=None, env=False, monkeypatch=None):
    with open(CFDRUN) as f:
        data = copy.deepcopy(json.load(f))
    mc = data["meshGeometry"].setdefault("meshConfig", {})
    mc["meshEngine"] = "dcCart"
    # put every rack of the fixture on one lattice (its fourth stands 0.1 mm low)
    for rack in data["meshGeometry"]["racks"].values():
        geom = rack["geometry"]
        geom["anchor"]["y"] = 1.0
        geom["unitHeight"] = 0.0445
        geom["quaternion"]["x"] = geom["quaternion"]["z"] = 0.0
    if aligned is not None:
        mc["slotAlignedBackground"] = aligned
    with tempfile.TemporaryDirectory() as tmp:
        case_dir, cfdrun = make_case(Path(tmp), data)
        r = STLGenRunner(case_dir, cfdrun)
        r._load()
        r.validate_geometry()
        r._preflight_partition_wall_thickness()
        r._ensure_watertight_room_enclosure()
        r._prepare_runtime_metadata()
        r._generate_mesh_dicts()
        base = float(mc.get("baseSize", 0.25) or 0.25)
        return r._bg_grid_planes["y"], base, data["meshGeometry"]["racks"]


def test_lattice_planes_at_the_exact_pitch(monkeypatch):
    monkeypatch.delenv("DCDTUNI_DCCART_SLOT_ALIGN", raising=False)
    planes, base, racks = _planes(aligned=True)
    lattice = _rack_slot_lattice(racks)
    assert lattice is not None
    origin, _ = lattice
    steps = {round(b - a, 9) for a, b in zip(planes, planes[1:])}
    assert steps == {round(base, 9)}
    assert any(abs(p - origin) < 1e-9 for p in planes)


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("DCDTUNI_DCCART_SLOT_ALIGN", raising=False)
    default, _, _ = _planes()
    off, _, _ = _planes(aligned=False)
    assert default == off


def test_environment_switch_still_works(monkeypatch):
    monkeypatch.setenv("DCDTUNI_DCCART_SLOT_ALIGN", "1")
    env, _, _ = _planes()
    monkeypatch.delenv("DCDTUNI_DCCART_SLOT_ALIGN")
    key, _, _ = _planes(aligned=True)
    assert env == key


def test_the_choice_travels_and_validates():
    from pydantic import ValidationError
    from backend.aircooling.runner.cfdrun_schema import MeshConfig
    from backend.aircooling.runner.derived_config import derive_mesh_config
    assert derive_mesh_config({"slotAlignedBackground": True}, {}, block_mesh_bounds=None)["slotAlignedBackground"] is True
    assert "slotAlignedBackground" not in derive_mesh_config({}, {}, block_mesh_bounds=None)
    assert MeshConfig(slotAlignedBackground=True).slotAlignedBackground is True
    with pytest.raises(ValidationError):
        MeshConfig(slotAlignedBackground="yes please")
