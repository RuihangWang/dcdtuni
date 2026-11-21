"""Tests that room-boundary floors (defaultBC='wall') are NOT treated as internal baffles.

Regression test for a bug where the room2D editor set isRoomBoundary=true but
defaultBC.type='wallBaffle', causing the mesh generator to attempt dual-side
seed placement on room boundary floors and crash.

Updated 2026-06-14 (P4): the tracked case files are now schemaVersion 4, so the
floor data is read from the V4 shape via ``load_cfdproj_v4``:
``constructions.floors[uid]`` with ``cooling.defaultBoundaryType`` (was
``cfd.defaultBC.type``), ``cooling.roomBoundaryLocation`` (was
``cfd.roomBoundaryType``), ``isRoomBoundary``, and ``meta.name``.
"""
import json
import sys
import pathlib
from pathlib import Path

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src/frontend/scripts"))
from cfdproj_migrate import load_cfdproj_v4

from backend.aircooling.runner.stlgen_runner import STLGenRunner

SLAB_DIR = Path(__file__).resolve().parents[3] / "case" / "Slab"
SLAB_CFDPROJ = SLAB_DIR / "Slab.cfdproj"
SLAB_CFDRUN = SLAB_DIR / "model" / "Slab.cfdrun"


def _floors(v4_doc):
    """Yield (name, floor_dict) for every floor in a V4 cfdproj document."""
    floors = (v4_doc.get("constructions") or {}).get("floors") or {}
    for f in floors.values():
        yield (f.get("meta") or {}).get("name"), f


def _default_bc(floor):
    return (floor.get("cooling") or {}).get("defaultBoundaryType", "wall")


def _is_boundary(floor):
    rbl = (floor.get("cooling") or {}).get("roomBoundaryLocation", "none")
    return bool(floor.get("isRoomBoundary", False)) or rbl in ("top", "bottom")


@pytest.fixture
def slab_cfdproj_data():
    if not SLAB_CFDPROJ.exists():
        pytest.skip(f"Slab cfdproj not found: {SLAB_CFDPROJ}")
    return load_cfdproj_v4(SLAB_CFDPROJ)


@pytest.fixture
def slab_cfdrun_data():
    if not SLAB_CFDRUN.exists():
        pytest.skip(f"Slab cfdrun not found: {SLAB_CFDRUN}")
    with open(SLAB_CFDRUN) as f:
        return json.load(f)


def test_room_boundary_floor_not_wallbaffle(slab_cfdproj_data):
    """Room boundary floors must NOT have defaultBoundaryType='wallBaffle'.

    Regression: the room2D editor set isRoomBoundary=true but left the floor's
    default BC at 'wallBaffle'. The mesh generator uses that BC to decide
    dual-side seed placement, so a room boundary floor with wallBaffle crashes
    snappyHexMesh.
    """
    for name, floor in _floors(slab_cfdproj_data):
        if floor.get("isRoomBoundary", False):
            bc_type = _default_bc(floor)
            assert bc_type != "wallBaffle", (
                f"Room boundary floor '{name}' has isRoomBoundary=True but "
                f"defaultBoundaryType='{bc_type}' — will cause dual-side seed crash"
            )


def test_internal_floor_has_wallbaffle(slab_cfdproj_data):
    """Internal raised floors must have isRoomBoundary=False and defaultBoundaryType='wallBaffle'."""
    for name, floor in _floors(slab_cfdproj_data):
        if name != "Raising_floor":
            continue
        assert floor.get("isRoomBoundary") is False
        bc_type = _default_bc(floor)
        assert bc_type == "wallBaffle", (
            f"Raised floor should have defaultBoundaryType='wallBaffle', got '{bc_type}'"
        )
        return
    pytest.fail("Raising_floor not found in Slab cfdproj")


def test_cfdrun_floor_defaultbc_matches_boundary_status(slab_cfdrun_data):
    """The .cfdrun export must have defaultBC='wall' for room boundary floors.

    In cfdrun V2 the geometry blob lives under meshGeometry (the runner validates
    it via CFDExport.model_validate(raw["meshGeometry"])), so floors are nested at
    meshGeometry.floors, not at the top level.
    """
    floors = (slab_cfdrun_data.get("meshGeometry") or {}).get("floors", {})
    assert "Ground_floor" in floors
    assert "Ceiling" in floors
    assert "Raising_floor" in floors

    assert floors["Ground_floor"].get("defaultBC") == "wall"
    assert floors["Ceiling"].get("defaultBC") == "wall"
    assert floors["Raising_floor"].get("defaultBC") == "wallBaffle"


def test_all_cfdproj_room_boundary_consistency():
    """Room boundary floors must not have defaultBoundaryType='wallBaffle'.

    Checks both signals: isRoomBoundary=True and roomBoundaryLocation in
    (top, bottom). Either one being set means the floor is a room boundary
    surface and must use a 'wall' default BC — otherwise the mesh generator
    creates dual-side seeds and crashes.
    """
    case_dir = Path(__file__).resolve().parents[3] / "case"
    issues = []

    for cfdproj in sorted(case_dir.glob("*/*.cfdproj")):
        try:
            data = load_cfdproj_v4(cfdproj)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        for name, floor in _floors(data):
            if _is_boundary(floor) and _default_bc(floor) == "wallBaffle":
                issues.append(
                    f"{cfdproj.parent.name}/{name}: room boundary floor with "
                    f"defaultBoundaryType='wallBaffle' — will cause dual-side seed crash"
                )

    assert issues == [], (
        f"Found {len(issues)} floor configuration inconsistencies:\n"
        + "\n".join(f"  - {i}" for i in issues)
    )
