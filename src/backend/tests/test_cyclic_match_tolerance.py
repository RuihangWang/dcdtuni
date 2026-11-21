"""fix_cyclic_match_tolerance must be idempotent.

Mesh-only normalises polyMesh/boundary and then fingerprints the mesh;
solve-only normalises again and then checks that fingerprint. The function
used to flip between two states on every call (adding, then deleting, a
second matchTolerance), so every case with cyclic perforated-panel patches -
Base604 at 0.25 m - failed "solve" after "mesh" with "source-surface mesh
source identity does not match the .cfdrun and current mesh".
"""
from pathlib import Path

import pytest

from backend.aircooling.runner.openfoam_case import fix_cyclic_match_tolerance

HEADER = "FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       polyBoundaryMesh;\n}\n\n3\n(\n"

# dcCart writes the tolerance BEFORE neighbourPatch
DCCART_PAIR = """    panel_master
    {
        type            cyclic;
        inGroups        List<word> 1(cyclic);
        nFaces          4;
        startFace       100;
        matchTolerance  0.0001;
        transform       coincidentFullMatch;
        neighbourPatch  panel_slave;
    }
    panel_slave
    {
        type            cyclic;
        inGroups        List<word> 1(cyclic);
        nFaces          4;
        startFace       104;
        matchTolerance  0.0001;
        transform       coincidentFullMatch;
        neighbourPatch  panel_master;
    }
"""

# snappy/createBaffles writes it AFTER neighbourPatch, with an unknown transform
SNAPPY_PAIR = """    door_master
    {
        type            cyclic;
        inGroups        List<word> 2(cyclic wall);
        nFaces          2;
        startFace       100;
        transform       unknown;
        neighbourPatch  door_slave;
        matchTolerance  0.0001;
    }
    door_slave
    {
        type            cyclic;
        inGroups        List<word> 2(cyclic wall);
        nFaces          2;
        startFace       102;
        transform       unknown;
        neighbourPatch  door_master;
        matchTolerance  0.0001;
    }
"""

WALL = """    room
    {
        type            wall;
        inGroups        List<word> 1(wall);
        nFaces          10;
        startFace       108;
    }
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "boundary"
    path.write_text(HEADER + body + ")\n", encoding="utf-8")
    return path


def _patch(text: str, name: str) -> str:
    start = text.index(f"    {name}\n")
    return text[start:text.index("    }", start)]


@pytest.mark.parametrize("pair", [DCCART_PAIR, SNAPPY_PAIR], ids=["dccart", "snappy"])
def test_each_cyclic_patch_ends_with_exactly_one_tolerance_and_a_second_call_changes_nothing(tmp_path, pair):
    path = _write(tmp_path, pair + WALL)
    fix_cyclic_match_tolerance(path)
    once = path.read_text(encoding="utf-8")
    for name in ("master", "slave"):
        block = _patch(once, f"panel_{name}" if pair is DCCART_PAIR else f"door_{name}")
        assert block.count("matchTolerance") == 1, block
        assert "matchTolerance  0.01;" in block
        assert "transform       coincidentFullMatch;" in block
        assert "wall)" not in block
    for _ in range(3):
        fix_cyclic_match_tolerance(path)
        assert path.read_text(encoding="utf-8") == once


def test_non_cyclic_patches_and_files_without_cyclics_are_untouched(tmp_path):
    path = _write(tmp_path, WALL)
    before = path.read_text(encoding="utf-8")
    fix_cyclic_match_tolerance(path)
    assert path.read_text(encoding="utf-8") == before
    path = _write(tmp_path, DCCART_PAIR + WALL)
    fix_cyclic_match_tolerance(path)
    assert _patch(path.read_text(encoding="utf-8"), "room") == _patch(HEADER + WALL + ")\n", "room")


def test_an_already_normal_file_is_not_rewritten(tmp_path):
    path = _write(tmp_path, DCCART_PAIR)
    fix_cyclic_match_tolerance(path)
    mtime = path.stat().st_mtime_ns
    fix_cyclic_match_tolerance(path)
    assert path.stat().st_mtime_ns == mtime
