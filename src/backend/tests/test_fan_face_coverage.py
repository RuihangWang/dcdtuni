"""An open server box: a fan face that kept only part of its area in the mesh.

CMI-Guichou's row J lost a third of its NF5280M5 fan faces when the mesher
split each server box into open pieces; the patches still existed, so the name
check passed, and about 11 m3/s of exhaust crossed into the cold aisle.
"""

from __future__ import annotations

from pathlib import Path

from backend.aircooling.runner.mesh_acceptance import (
    check_patch_solid_correspondence,
    fan_face_shortfalls,
)

_HEADER = "FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class {cls};\n    object {obj};\n}}\n"


def _square(name: str, z: float) -> str:
    """A unit square in the plane z, as two facets."""
    tri = lambda a, b, c: (  # noqa: E731
        "  facet normal 0 0 1\n    outer loop\n"
        + "".join(f"      vertex {x} {y} {z}\n" for x, y in (a, b, c))
        + "    endloop\n  endfacet\n"
    )
    return f"solid {name}\n" + tri((0, 0), (1, 0), (1, 1)) + tri((0, 0), (1, 1), (0, 1)) + f"endsolid {name}\n"


def _case(tmp_path: Path, outlet_width: float) -> Path:
    """Inlet at z=0 kept whole; outlet at z=1 kept `outlet_width` of its width."""
    mesh = tmp_path / "Mesh"
    tri = mesh / "constant" / "triSurface"
    tri.mkdir(parents=True)
    (tri / "merged_server_1U.stl").write_text(
        _square("S1_inlet_patch", 0.0) + _square("S1_outlet_p0", 1.0), encoding="utf-8"
    )
    poly = mesh / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    w = outlet_width
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (w, 0, 1), (w, 1, 1), (0, 1, 1)]
    (poly / "points").write_text(
        _HEADER.format(cls="vectorField", obj="points")
        + f"{len(pts)}\n(\n" + "".join(f"({x} {y} {z})\n" for x, y, z in pts) + ")\n",
        encoding="utf-8",
    )
    (poly / "faces").write_text(
        _HEADER.format(cls="faceList", obj="faces") + "2\n(\n4(0 1 2 3)\n4(4 5 6 7)\n)\n",
        encoding="utf-8",
    )
    (poly / "boundary").write_text(
        _HEADER.format(cls="polyBoundaryMesh", obj="boundary")
        + "2\n(\n"
        + "    S1_inlet_patch\n    {\n        type patch;\n        nFaces 1;\n        startFace 0;\n    }\n"
        + "    S1_outlet_p0\n    {\n        type patch;\n        nFaces 1;\n        startFace 1;\n    }\n"
        + ")\n",
        encoding="utf-8",
    )
    return mesh


def test_a_whole_server_box_is_not_reported(tmp_path):
    assert fan_face_shortfalls(_case(tmp_path, 1.0)) == []


def test_quantisation_is_not_an_open_box(tmp_path):
    assert fan_face_shortfalls(_case(tmp_path, 0.9)) == []


def test_a_fan_face_that_lost_a_third_is_reported(tmp_path):
    short = fan_face_shortfalls(_case(tmp_path, 0.63))
    assert [(n, round(a, 6), round(m, 6)) for n, a, m in short] == [("S1_outlet_p0", 1.0, 0.63)]


def test_the_acceptance_gate_fails_on_it(tmp_path):
    mesh = _case(tmp_path, 0.63)
    (mesh / "system").mkdir()
    (mesh / "system" / "snappyHexMeshDict").write_text(
        "refinementSurfaces\n{\n    merged_server_1U\n    {\n        level (1 1);\n"
        "        regions\n        {\n            S1_inlet_patch { level (1 1); }\n"
        "            S1_outlet_p0 { level (1 1); }\n        }\n    }\n}\n",
        encoding="utf-8",
    )
    result = check_patch_solid_correspondence(mesh, case_name="open-box")
    assert result.open_server_faces == ["S1_outlet_p0"]
    assert not result.ok
    assert "OPEN-SERVER 1" in result.summary()


def _case_with_lost_outlet(tmp_path: Path) -> Path:
    """The outlet patch got no face at all (Base201_GPU on a 44.64 mm grid for 44.45 mm slots)."""
    mesh = _case(tmp_path, 1.0)
    boundary = mesh / "constant" / "polyMesh" / "boundary"
    text = boundary.read_text(encoding="utf-8")
    text = text.replace("S1_inlet_patch\n    {\n        type patch;\n        nFaces 1;",
                        "S1_inlet_patch\n    {\n        type patch;\n        nFaces 2;")
    text = text.replace("S1_outlet_p0\n    {\n        type patch;\n        nFaces 1;\n        startFace 1;",
                        "S1_outlet_p0\n    {\n        type patch;\n        nFaces 0;\n        startFace 2;")
    boundary.write_text(text, encoding="utf-8")
    return mesh


def test_a_lost_fan_face_fails_the_mesh(tmp_path):
    # A fan face with NO mesh face is not an open box to warn about: the solve
    # cannot build the server's boundary condition at all (fields.py refuses a
    # fan outlet without its inlet), so the mesh stage says so and fails.
    import pytest
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    mesh = _case_with_lost_outlet(tmp_path)
    assert [n for n, _, m in fan_face_shortfalls(mesh) if m == 0.0] == ["S1_outlet_p0"]
    runner = STLGenRunner.__new__(STLGenRunner)
    with pytest.raises(RuntimeError, match="S1_outlet_p0"):
        runner._check_server_fan_faces(mesh)


def test_a_short_fan_face_only_warns(tmp_path):
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    runner = STLGenRunner.__new__(STLGenRunner)
    runner._check_server_fan_faces(_case(tmp_path, 0.63))   # warns, does not raise
