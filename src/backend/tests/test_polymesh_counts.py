"""Unit tests for polyMesh cell/face counts helper (2026-04-19).

Drives ``MeshPreviewLoader.read_counts`` — used by the STLGen
``/api/mesh-preview/load`` endpoint to report total volume cells and
total boundary faces alongside the existing surface stats.
"""
from pathlib import Path

import pytest

from backend.aircooling.runner.postprocess import MeshPreviewLoader


OWNER_HEADER_WITH_NOTE = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       labelList;
    note        "nPoints:223945 nCells:752390 nFaces:2350123 nInternalFaces:1988124";
    location    "constant/polyMesh";
    object      owner;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

1988124
(
0
1
)
"""

OWNER_HEADER_NO_NOTE = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       labelList;
    location    "constant/polyMesh";
    object      owner;
}

1988124
(
0
1
)
"""

BOUNDARY_TEXT = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       polyBoundaryMesh;
    location    "constant/polyMesh";
    object      boundary;
}

3
(
    room_wall
    {
        type            wall;
        inGroups        List<word> 1(wall);
        nFaces          1000;
        startFace       1988124;
    }
    rack_inlet
    {
        type            patch;
        nFaces          500;
        startFace       1989124;
    }
    rack_outlet
    {
        type            patch;
        nFaces          250;
        startFace       1989624;
    }
)
"""


def _write_polymesh(tmp_path: Path, owner_text: str, boundary_text: str) -> Path:
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "owner").write_text(owner_text)
    (poly / "boundary").write_text(boundary_text)
    return tmp_path


def test_read_counts_from_owner_note(tmp_path):
    """Owner header note gives nCells/nFaces/nInternalFaces directly."""
    _write_polymesh(tmp_path, OWNER_HEADER_WITH_NOTE, BOUNDARY_TEXT)
    loader = MeshPreviewLoader(tmp_path)

    counts = loader.read_counts()

    assert counts["nCells"] == 752390
    assert counts["nFaces"] == 2350123
    assert counts["nInternalFaces"] == 1988124
    assert counts["nBoundaryFaces"] == 2350123 - 1988124  # 361999


def test_read_counts_boundary_sum_matches_nfaces_minus_internal(tmp_path):
    """Sum of boundary patch nFaces must equal nFaces - nInternalFaces."""
    _write_polymesh(tmp_path, OWNER_HEADER_WITH_NOTE, BOUNDARY_TEXT)
    loader = MeshPreviewLoader(tmp_path)

    counts = loader.read_counts()

    assert counts["nBoundarySum"] == 1000 + 500 + 250


def test_read_counts_falls_back_to_boundary_when_note_missing(tmp_path):
    """Owner file without note → nCells=None, boundary sum still reported."""
    _write_polymesh(tmp_path, OWNER_HEADER_NO_NOTE, BOUNDARY_TEXT)
    loader = MeshPreviewLoader(tmp_path)

    counts = loader.read_counts()

    assert counts["nCells"] is None
    assert counts["nInternalFaces"] is None
    assert counts["nBoundaryFaces"] is None
    assert counts["nBoundarySum"] == 1750


def test_read_counts_missing_polymesh_returns_empty(tmp_path):
    """No polyMesh dir → all counts None/0 (not an exception)."""
    loader = MeshPreviewLoader(tmp_path)

    counts = loader.read_counts()

    assert counts["nCells"] is None
    assert counts["nBoundarySum"] == 0
