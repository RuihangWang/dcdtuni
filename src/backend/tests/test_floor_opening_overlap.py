"""Floor-opening / floor-part consistency preflight (Base604, 2026-09-21).

A perforated (or wallBaffle) floor opening is its own zero-thickness sheet
inside a hole cut out of the floor's remaining part(s), and both come from ONE
browser export. When the two land on disk from different exports — a June
``perforated_floor_<x>.stl`` kept beside a fresh
``wallBaffle_floor_<floor>_part1.stl`` after the opening's depth was edited —
the opening overhangs the hole. The overlap strip is coplanar with the floor
part, the cut-cell mesher tags it to the part's zone, and the baffle coverage
gate refuses the whole mesh ten minutes later with a ratio (0.980) that says
nothing about the cause. Measured on Base604: patch_2 Z from 0.030697 (June)
against a part1 hole starting at Z=0.05 (fresh) = 19.3 mm x 9.6 m = 0.185 m2,
level-invariant because it is geometry.

The preflight measures that overlap from the STL files before meshing and
fails in a second with the fix in the message.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.aircooling.runner.floor_consistency import (
    FloorOverlap,
    check_floor_openings,
    format_floor_overlaps,
    overlap_area,
    solid_triangles,
)
from backend.aircooling.runner.stlgen_runner import STLGenRunner, _sanitize


Y = 0.8


def _rect(x0: float, x1: float, z0: float, z1: float, y: float = Y):
    """Two CCW-consistent triangles tiling the rectangle at height y."""
    a, b, c, d = (x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)
    return [(a, b, c), (a, c, d)]


def _stl(path: Path, name: str, tris) -> None:
    body = [f"solid {name}"]
    for t in tris:
        body.append("  facet normal 0 1 0\n    outer loop")
        for v in t:
            body.append(f"      vertex {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
        body.append("    endloop\n  endfacet")
    body.append(f"endsolid {name}\n")
    path.write_text("\n".join(body), encoding="utf-8")


def _part_with_hole(hz0: float, hz1: float, hx0: float = 1.8, hx1: float = 11.4):
    """A 15.6 x 24.6 floor sheet with one rectangular hole, as four strips."""
    tris = []
    tris += _rect(0.0, hx0, -1.2, 23.4)          # left strip
    tris += _rect(hx1, 15.6, -1.2, 23.4)         # right strip
    tris += _rect(hx0, hx1, -1.2, hz0)           # strip below the hole
    tris += _rect(hx0, hx1, hz1, 23.4)           # strip above the hole
    return tris


PATCH_JUNE = _rect(1.8, 11.4, 0.030697, 1.0)     # the June export's patch_2
PATCH_FRESH = _rect(1.8, 11.4, 0.05, 1.0)        # the fresh export's patch_2


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def test_overlap_of_mixed_export_is_the_19mm_strip():
    part_fresh = _part_with_hole(0.05, 1.0)
    a = overlap_area(PATCH_JUNE, part_fresh, axis=1)
    assert a == pytest.approx(9.6 * (0.05 - 0.030697), abs=1e-9)


@pytest.mark.parametrize("patch, hole", [(PATCH_JUNE, (0.030697, 1.0)), (PATCH_FRESH, (0.05, 1.0))])
def test_consistent_export_has_zero_overlap(patch, hole):
    assert overlap_area(patch, _part_with_hole(*hole), axis=1) == pytest.approx(0.0, abs=1e-12)


def test_overlap_is_symmetric_and_bounded_by_the_opening():
    part = _part_with_hole(0.05, 1.0)
    ab = overlap_area(PATCH_JUNE, part, axis=1)
    ba = overlap_area(part, PATCH_JUNE, axis=1)
    assert ab == pytest.approx(ba, abs=1e-9)
    assert ab < 9.6 * (1.0 - 0.030697)


def test_opening_fully_over_solid_floor_overlaps_completely():
    # no hole at all: the whole opening lies on the part
    part = _rect(0.0, 15.6, -1.2, 23.4)
    assert overlap_area(PATCH_FRESH, part, axis=1) == pytest.approx(9.12, abs=1e-9)


# ---------------------------------------------------------------------------
# the check over a triSurface directory
# ---------------------------------------------------------------------------

def _floors():
    return {
        "Room_Raising_Floor": {
            "defaultBC": "wallBaffle",
            "openings": [
                {"solidName": "Room_Raising_Floor_patch_1", "physics": {"type": "perforated"}},
                {"solidName": "Room_Raising_Floor_patch_2", "physics": {"type": "perforated"}},
            ],
        }
    }


@pytest.fixture
def tri_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Mesh" / "constant" / "triSurface"
    d.mkdir(parents=True)
    return d


def _write_set(tri_dir: Path, *, patch2, hole_z0: float) -> None:
    _stl(tri_dir / "perforated_floor_Room_Raising_Floor_patch_1.stl",
         "Room_Raising_Floor_patch_1", _rect(1.8, 11.4, 4.6, 5.8))
    _stl(tri_dir / "perforated_floor_Room_Raising_Floor_patch_2.stl",
         "Room_Raising_Floor_patch_2", patch2)
    # part1 = floor with the patch_1 hole AND the patch_2 hole
    part = []
    part += _rect(0.0, 1.8, -1.2, 23.4) + _rect(11.4, 15.6, -1.2, 23.4)
    part += _rect(1.8, 11.4, -1.2, hole_z0) + _rect(1.8, 11.4, 1.0, 4.6) + _rect(1.8, 11.4, 5.8, 23.4)
    _stl(tri_dir / "wallBaffle_floor_Room_Raising_Floor_part1.stl", "Room_Raising_Floor_part1", part)
    # an unrelated solid on another plane must never be paired with the floor
    _stl(tri_dir / "merged_floor_ceiling.stl", "Room_Floor", _rect(0.0, 15.6, -1.2, 23.4, y=0.0))


def test_solid_triangles_reads_every_mesh_input(tri_dir: Path):
    _write_set(tri_dir, patch2=PATCH_FRESH, hole_z0=0.05)
    tris = solid_triangles(tri_dir)
    assert set(tris) == {"Room_Raising_Floor_patch_1", "Room_Raising_Floor_patch_2",
                         "Room_Raising_Floor_part1", "Room_Floor"}
    assert len(tris["Room_Raising_Floor_patch_2"]) == 2


def test_check_reports_the_mixed_set(tri_dir: Path):
    _write_set(tri_dir, patch2=PATCH_JUNE, hole_z0=0.05)
    found = check_floor_openings(tri_dir, _floors(), _sanitize)
    assert len(found) == 1
    ov: FloorOverlap = found[0]
    assert ov.floor == "Room_Raising_Floor"
    assert ov.opening == "Room_Raising_Floor_patch_2"
    assert ov.part == "Room_Raising_Floor_part1"
    assert ov.overlap_m2 == pytest.approx(0.185309, abs=1e-5)
    assert ov.fraction == pytest.approx(0.185309 / 9.30531, rel=1e-3)
    assert ov.plane_axis == 1 and ov.plane_coord == pytest.approx(0.8)
    msg = format_floor_overlaps(found)
    assert "Room_Raising_Floor_patch_2" in msg and "Room_Raising_Floor_part1" in msg
    assert "0.185" in msg and "re-export" in msg.lower()


@pytest.mark.parametrize("patch2, hole_z0", [(PATCH_JUNE, 0.030697), (PATCH_FRESH, 0.05)])
def test_check_accepts_consistent_sets(tri_dir: Path, patch2, hole_z0):
    _write_set(tri_dir, patch2=patch2, hole_z0=hole_z0)
    assert check_floor_openings(tri_dir, _floors(), _sanitize) == []


def test_check_ignores_sub_threshold_and_missing_solids(tri_dir: Path):
    # a 1e-6 m overhang (export rounding) is below the 1e-4 fraction threshold
    _write_set(tri_dir, patch2=_rect(1.8, 11.4, 0.05 - 1e-6, 1.0), hole_z0=0.05)
    assert check_floor_openings(tri_dir, _floors(), _sanitize) == []
    # an opening declared in the manifest but absent on disk is not an overlap
    floors = _floors()
    floors["Room_Raising_Floor"]["openings"].append(
        {"solidName": "Room_Raising_Floor_patch_9", "physics": {"type": "perforated"}})
    assert check_floor_openings(tri_dir, floors, _sanitize) == []


# ---------------------------------------------------------------------------
# runner hook
# ---------------------------------------------------------------------------

def test_runner_preflight_refuses_mixed_set_with_the_fix(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DCDTUNI_FLOOR_OVERLAP_CHECK", raising=False)
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r.tri_surface_dir.mkdir(parents=True)
    _write_set(r.tri_surface_dir, patch2=PATCH_JUNE, hole_z0=0.05)
    r._data = {"floors": _floors()}
    with pytest.raises(RuntimeError) as exc:
        r._preflight_floor_openings()
    text = str(exc.value)
    assert "Room_Raising_Floor_patch_2" in text and "re-export" in text.lower()
    assert "DCDTUNI_FLOOR_OVERLAP_CHECK" in text


def test_runner_preflight_passes_consistent_set_and_honours_override(tmp_path: Path, monkeypatch):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r.tri_surface_dir.mkdir(parents=True)
    _write_set(r.tri_surface_dir, patch2=PATCH_FRESH, hole_z0=0.05)
    r._data = {"floors": _floors()}
    r._preflight_floor_openings()  # consistent: no raise
    _write_set(r.tri_surface_dir, patch2=PATCH_JUNE, hole_z0=0.05)
    monkeypatch.setenv("DCDTUNI_FLOOR_OVERLAP_CHECK", "off")
    r._preflight_floor_openings()  # overridden: warns, does not raise


def test_runner_preflight_is_a_noop_without_floors(tmp_path: Path):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r._data = {}
    r._preflight_floor_openings()
