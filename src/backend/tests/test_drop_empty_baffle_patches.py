"""`keepPatches true` and `createBaffles` collide; the collision is resolved here.

Keeping zero-sized patches is what stops a flush-mounted ACU bottom losing its
boundary condition. But the snappyHexMesh branch it disables is literally
"remove zero sized patches originating from faceZones", so the
``<zone>_master`` / ``<zone>_slave`` patches snappy makes for a baffle faceZone
survive too — as type ``wall``. createBaffles then finds the name taken and dies
with "Attempt to cast type wall to type cyclic" (Base2-9 failed exactly so).

snappy has no per-surface keepPatches, so the runner drops precisely the empty
patches createBaffles is about to create — and nothing else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _runner() -> STLGenRunner:
    r = STLGenRunner.__new__(STLGenRunner)
    r._data = {}
    return r


def _write_boundary(path: Path, patches):
    """patches: [(name, type, nFaces, startFace), ...]"""
    body = "".join(
        f"\n    {n}\n    {{\n        type            {t};\n"
        f"        nFaces          {nf};\n        startFace       {sf};\n    }}\n"
        for n, t, nf, sf in patches
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
        "    class polyBoundaryMesh;\n    object boundary;\n}\n"
        f"\n{len(patches)}\n({body})\n\n"
        "// ************************************************************ //\n",
        encoding="utf-8",
    )


def _write_baffles_dict(path: Path, *zones: str):
    blocks = "".join(
        f"    {z}\n    {{\n        type faceZone;\n        zoneName {z};\n"
        "        patches\n        {\n"
        f"            master {{ name {z}_master; type cyclic;"
        f" neighbourPatch {z}_slave; }}\n"
        f"            slave  {{ name {z}_slave;  type cyclic;"
        f" neighbourPatch {z}_master; }}\n"
        "        }\n    }\n"
        for z in zones
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "FoamFile\n{\n    version 2.0;\n    object createBafflesDict;\n}\n"
        f"internalFacesOnly true;\nbaffles\n{{\n{blocks}}}\n",
        encoding="utf-8",
    )


def _patch_names(path: Path):
    import re

    return re.findall(r"^    ([A-Za-z_][\w.]*)\s*$", path.read_text(), re.MULTILINE)


def _declared_count(path: Path) -> int:
    import re

    return int(re.search(r"\n(\d+)\n\(", path.read_text()).group(1))


def test_empty_colliding_patch_is_dropped(tmp_path):
    boundary = tmp_path / "constant/polyMesh/boundary"
    _write_boundary(
        boundary,
        [
            ("Room_Floor", "wall", 120, 0),
            ("Raised_Floor_slave", "wall", 0, 120),
            ("ACU_1_Bottom", "wall", 0, 120),
        ],
    )
    _write_baffles_dict(tmp_path / "system/createBafflesDict", "Raised_Floor")

    assert _runner()._drop_empty_baffle_patches(tmp_path) == 1

    names = _patch_names(boundary)
    assert "Raised_Floor_slave" not in names
    assert _declared_count(boundary) == 2


def test_the_flush_mounted_patch_keepPatches_saved_is_not_dropped(tmp_path):
    """ACU_1_Bottom is empty too, but it is not a createBaffles target — it is
    exactly the patch keepPatches exists to preserve."""
    boundary = tmp_path / "constant/polyMesh/boundary"
    _write_boundary(
        boundary,
        [("Room_Floor", "wall", 120, 0), ("ACU_1_Bottom", "wall", 0, 120)],
    )
    _write_baffles_dict(tmp_path / "system/createBafflesDict", "Raised_Floor")

    assert _runner()._drop_empty_baffle_patches(tmp_path) == 0
    assert "ACU_1_Bottom" in _patch_names(boundary)


def test_a_populated_baffle_patch_is_never_dropped(tmp_path):
    """Only EMPTY collisions are the problem. A patch with faces is real data."""
    boundary = tmp_path / "constant/polyMesh/boundary"
    _write_boundary(
        boundary,
        [("Room_Floor", "wall", 120, 0), ("Raised_Floor_master", "cyclic", 44, 120)],
    )
    _write_baffles_dict(tmp_path / "system/createBafflesDict", "Raised_Floor")

    assert _runner()._drop_empty_baffle_patches(tmp_path) == 0
    assert "Raised_Floor_master" in _patch_names(boundary)


def test_both_halves_of_a_pair_are_dropped(tmp_path):
    boundary = tmp_path / "constant/polyMesh/boundary"
    _write_boundary(
        boundary,
        [
            ("Room_Floor", "wall", 120, 0),
            ("Raised_Floor_master", "wall", 0, 120),
            ("Raised_Floor_slave", "wall", 0, 120),
        ],
    )
    _write_baffles_dict(tmp_path / "system/createBafflesDict", "Raised_Floor")

    assert _runner()._drop_empty_baffle_patches(tmp_path) == 2
    assert _declared_count(boundary) == 1


def test_processor_directory_boundary_can_be_targeted(tmp_path):
    """Parallel createBaffles reads processor*/constant/polyMesh, not the case."""
    _write_baffles_dict(tmp_path / "system/createBafflesDict", "Raised_Floor")
    proc = tmp_path / "processor0"
    boundary = proc / "constant/polyMesh/boundary"
    _write_boundary(
        boundary,
        [("Room_Floor", "wall", 60, 0), ("Raised_Floor_slave", "wall", 0, 60)],
    )

    assert _runner()._drop_empty_baffle_patches(tmp_path, proc) == 1
    assert "Raised_Floor_slave" not in _patch_names(boundary)


def test_no_baffles_dict_is_a_no_op(tmp_path):
    boundary = tmp_path / "constant/polyMesh/boundary"
    _write_boundary(boundary, [("Room_Floor", "wall", 120, 0)])
    assert _runner()._drop_empty_baffle_patches(tmp_path) == 0


def test_missing_boundary_file_is_a_no_op(tmp_path):
    _write_baffles_dict(tmp_path / "system/createBafflesDict", "Raised_Floor")
    assert _runner()._drop_empty_baffle_patches(tmp_path) == 0
