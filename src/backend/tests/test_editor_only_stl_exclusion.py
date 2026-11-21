"""WP-W7 — the editor-only interface STL bucket must never enter the mesh path.

The frontend writes each server's left/right INTERFACE faces — the planes
where a server touches a rack-frame cavity wall — into
``merged_server_interface_walls.stl`` under ``Mesh/constant/triSurface/``
purely for editor export completeness. There is NO FLUID at these interfaces:
the closed fluid partition is carried by the frame cavity walls plus the
servers' fluid-side faces. The mesh path must therefore ignore the file
completely — no patch, no boundary condition, no refinement shell, no
patch-geometry entry, no acceptance solid.

Pinned here, at every unfiltered consumption point:

* ``is_editor_only_stl`` — the ONE predicate both the runner and
  mesh_acceptance filter through (exact bucket name + the
  ``merged_server_*_left/_right.stl`` per-side split forms), while every
  legitimate mesh-input name (static grouped names, per-bucket ``<N>U`` /
  ``<N>U__<zone>`` files, partition-hole/leakage/baffle helpers) stays in.
* ``STLGenRunner._load`` discovery — the interface bucket never reaches
  ``_stl_solids``, which is the single source feeding snappyHexMeshDict
  geometry/refinementSurfaces (and through it dcCart, which reads the same
  dict), createBafflesDict faceZones and the split/restore passes — while a
  real server bucket in the same directory is still discovered.
* ``STLGenRunner._compute_patch_geometry`` — the raw-glob measurement feeding
  FieldGenerator's inlet BCs must not carry interface solids (whose names
  could otherwise shadow a real patch's area/perimeter).

The acceptance-side exclusion (mesh_acceptance readers) is pinned in
``test_mesh_acceptance.py``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.aircooling.runner.mesh_acceptance import is_editor_only_stl
from backend.aircooling.runner.stlgen_runner import STLGenRunner

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = (
    _REPO_ROOT / "src/backend/tests/fixtures/cfdrun_v2_full/Base_leakage.cfdrun"
)
_REAL_TRISURFACE = (
    _REPO_ROOT / "src/backend/tests/fixtures/cfdrun_cases/Base_leakage/triSurface"
)


def _ascii_stl(path: Path, *solids: str) -> None:
    """Minimal one-facet-per-solid ASCII STL (distinct geometry per solid)."""
    body = ""
    for i, s in enumerate(solids):
        z = 5.0 + float(i)
        body += (
            f"solid {s}\n"
            "  facet normal 0 0 1\n    outer loop\n"
            f"      vertex 0 0 {z}\n      vertex 1 0 {z}\n      vertex 0 1 {z}\n"
            "    endloop\n  endfacet\n"
            f"endsolid {s}\n"
        )
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def case_with_interface_bucket(tmp_path: Path) -> Path:
    """The frozen Base_leakage V2 export plus editor-only interface buckets.

    A copy, because ``_load()``'s in-place merge/split passes rewrite the
    triSurface they run on.
    """
    case_dir = tmp_path / "Base_leakage"
    (case_dir / "model").mkdir(parents=True)
    tri = case_dir / "Mesh" / "constant" / "triSurface"
    tri.mkdir(parents=True)
    shutil.copy(_FIXTURE, case_dir / "model" / "Base_leakage.cfdrun")
    stls = sorted(_REAL_TRISURFACE.glob("*.stl"))
    assert stls, f"Base_leakage triSurface fixture missing: {_REAL_TRISURFACE}"
    for stl in stls:
        shutil.copy(stl, tri / stl.name)
    # The WP-W7 bucket itself plus the hypothetical per-side split forms.
    _ascii_stl(
        tri / "merged_server_interface_walls.stl",
        "Server_1_left", "Server_1_right",
    )
    _ascii_stl(tri / "merged_server_interface_walls_left.stl", "Server_2_left")
    return case_dir


# ---------------------------------------------------------------------------
# The predicate — one definition, both consumers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "merged_server_interface_walls.stl",
        "merged_server_interface_walls_left.stl",
        "merged_server_interface_walls_right.stl",
        "merged_server_12U_left.stl",
        "merged_server_12U_right.stl",
    ],
)
def test_predicate_excludes_editor_only_names(name: str):
    assert is_editor_only_stl(name)
    assert is_editor_only_stl(Path("/case/Mesh/constant/triSurface") / name)


@pytest.mark.parametrize(
    "name",
    [
        # real bucket-file spellings (see _parse_bucket_from_filename)
        "merged_server_12U.stl",
        "merged_server_12U__Default_Rack_1.stl",
        "merged_through_zone_15U__Default_Rack_1_u1_u15.stl",
        "merged_blanking_3U.stl",
        "merged_partition_6U.stl",
        "merged_wallBaffle_2U.stl",
        # static grouped names + helper files
        "room_wall.stl",
        "merged_rack.stl",
        "merged_rack_frame.stl",
        "merged_acu.stl",
        "merged_floor_ceiling.stl",
        "merged_baffle.stl",
        "merged_wall_baffle.stl",
        "merged_leakage.stl",
        "partition_hole_Wall_1.stl",
        "leakage_gap_1.stl",
        "baffle_Slot19_top.stl",
        "perforated_floor_Raised_Floor_patch.stl",
        "wallBaffle_floor_Raised_Floor_part1.stl",
        # left/right as a TOKEN, never as the file suffix
        "merged_server_left_12U.stl",
    ],
)
def test_predicate_keeps_legitimate_mesh_inputs(name: str):
    assert not is_editor_only_stl(name)


# ---------------------------------------------------------------------------
# Runner consumption points
# ---------------------------------------------------------------------------

def test_runner_discovery_ignores_interface_bucket(case_with_interface_bucket: Path):
    """_load() discovery must leave the editor-only bucket out of _stl_solids —
    the map that feeds snappyHexMeshDict geometry/refinementSurfaces (hence
    dcCart, which reads the same dict), createBafflesDict and every split pass.
    Real server buckets in the same directory must still be discovered."""
    runner = STLGenRunner(case_with_interface_bucket)
    runner._load()

    assert "merged_server_interface_walls.stl" not in runner._stl_solids
    assert "merged_server_interface_walls_left.stl" not in runner._stl_solids

    every_solid = {s for names in runner._stl_solids.values() for s in names}
    assert not ({"Server_1_left", "Server_1_right", "Server_2_left"} & every_solid)

    # targeted exclusion, not a blanket merged_server_* rejection
    assert "merged_server_12U.stl" in runner._stl_solids
    assert runner._stl_solids["merged_server_12U.stl"]


def test_patch_geometry_skips_interface_bucket(case_with_interface_bucket: Path):
    """_compute_patch_geometry globs triSurface raw; the editor-only bucket
    must be skipped so an interface solid can never shadow a real patch's
    area/perimeter (the map is keyed by solid name, later files win)."""
    runner = STLGenRunner(case_with_interface_bucket)
    geom = runner._compute_patch_geometry()

    assert geom, "real solids must still be measured"
    for interface_solid in ("Server_1_left", "Server_1_right", "Server_2_left"):
        assert interface_solid not in geom
