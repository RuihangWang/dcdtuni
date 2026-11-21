"""Telling a real defect from a surface that correctly has no face.

A declared region with no patch can mean two opposite things, and the gate is
worthless if it cannot separate them:

* **UNRESOLVED** — a CELL occupies the gap the plane falls in, so that cell
  straddles the surface and the boundary condition has nothing to attach to.
  Faces merely existing somewhere either side is NOT enough: on Base2-9 the face
  planes around `A1_A1_DH_S6500_U45_top` run ... 2.70435 | 2.8 ... at ~0.012 m
  local spacing — an eight-cell gap where every cell was carved away. Putting a
  grid line there changed nothing (+113% cells, zero patches recovered).
* **buried / sealed** — nothing reaches the plane from one side or either. On
  CMI-Guichou, `ACU_A1_Left` sits at x=1.8 and rack frame `A3_frame` spans
  x 1.2..1.8 with the same y/z footprint: the rack is flush against the ACU, so
  the plane has solid on both sides and no fluid face can exist. Correct.

An earlier distance heuristic got `ACU_A1_Left` backwards — it saw cells 0.075 m
away and called them adjacent, when they were on the far side of the rack frame.
Counting faces on the plane answers it directly, so these tests build meshes
whose face positions are known exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aircooling.runner.mesh_acceptance import (
    check_patch_solid_correspondence,
)


def _quad_stl(path: Path, name: str, plane_x: float) -> None:
    """A zero-thickness quad at x = plane_x, spanning y,z in [0,1]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"solid {name}\n"
        "  facet normal 1 0 0\n    outer loop\n"
        f"      vertex {plane_x} 0 0\n      vertex {plane_x} 1 0\n"
        f"      vertex {plane_x} 0 1\n"
        "    endloop\n  endfacet\n"
        "  facet normal 1 0 0\n    outer loop\n"
        f"      vertex {plane_x} 1 0\n      vertex {plane_x} 1 1\n"
        f"      vertex {plane_x} 0 1\n"
        "    endloop\n  endfacet\n"
        f"endsolid {name}\n",
        encoding="utf-8",
    )


def _snappy(path: Path, surface: str, regions: list) -> None:
    inner = "".join(
        f"            {r}\n            {{\n                level (1 1);\n"
        "                patchInfo { type wall; }\n            }\n"
        for r in regions
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "FoamFile\n{\n    version 2.0;\n    object snappyHexMeshDict;\n}\n"
        "castellatedMeshControls\n{\n    refinementSurfaces\n    {\n"
        f"    {surface}\n    {{\n        level (1 1);\n"
        f"        regions\n        {{\n{inner}        }}\n    }}\n"
        "    }\n\n    refinementRegions\n    {\n    }\n}\n",
        encoding="utf-8",
    )


def _mesh(poly: Path, x_planes: list, patches: list) -> None:
    """A 1-D column of cells: one quad face per x in *x_planes*.

    Cells sit between consecutive planes, so a plane falling in a GAP between
    two face planes has a cell in it exactly when those planes bound one cell —
    which is the distinction the classifier turns on. Internal faces are written
    first, as polyMesh requires. The filler patch is `backgroundBoundary`, which
    the checker treats as synthetic, so the only thing these tests can fail on
    is the class under test.
    """
    poly.mkdir(parents=True, exist_ok=True)
    n_planes = len(x_planes)
    n_cells = max(1, n_planes - 1)

    # face order: interior planes (internal) first, then the two ends
    order = list(range(1, n_planes - 1)) + [0, n_planes - 1]
    owner, neighbour = [], []
    for k in range(n_planes - 2):
        owner.append(k)
        neighbour.append(k + 1)
    owner.append(0)                    # low end
    owner.append(max(0, n_cells - 1))  # high end

    pts, faces = [], []
    for plane_index in order:
        x = x_planes[plane_index]
        base = len(pts)
        pts += [(x, 0.0, 0.0), (x, 1.0, 0.0), (x, 1.0, 1.0), (x, 0.0, 1.0)]
        faces.append((base, base + 1, base + 2, base + 3))

    def _header(cls, obj, note=""):
        note_line = f'    note        "{note}";\n' if note else ""
        return (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            f"{note_line}    class {cls};\n    object {obj};\n}}\n// * * * //\n\n"
        )

    (poly / "points").write_text(
        _header("vectorField", "points")
        + f"{len(pts)}\n(\n"
        + "".join(f"({p[0]} {p[1]} {p[2]})\n" for p in pts)
        + ")\n",
        encoding="utf-8",
    )
    (poly / "faces").write_text(
        _header("faceList", "faces")
        + f"{len(faces)}\n(\n"
        + "".join("4(%d %d %d %d)\n" % f for f in faces)
        + ")\n",
        encoding="utf-8",
    )
    note = (
        f"nPoints:{len(pts)}  nCells:{n_cells}  "
        f"nFaces:{len(faces)}  nInternalFaces:{len(neighbour)}"
    )
    (poly / "owner").write_text(
        _header("labelList", "owner", note)
        + f"{len(owner)}\n(\n" + "".join(f"{v}\n" for v in owner) + ")\n",
        encoding="utf-8",
    )
    (poly / "neighbour").write_text(
        _header("labelList", "neighbour")
        + f"{len(neighbour)}\n(\n" + "".join(f"{v}\n" for v in neighbour) + ")\n",
        encoding="utf-8",
    )
    entries = "".join(
        f"    {p}\n    {{\n        type            wall;\n"
        f"        nFaces          1;\n        startFace       0;\n    }}\n"
        for p in patches
    )
    (poly / "boundary").write_text(
        "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
        "    class polyBoundaryMesh;\n    object boundary;\n}\n"
        f"\n{len(patches)}\n(\n{entries})\n",
        encoding="utf-8",
    )


@pytest.fixture()
def case(tmp_path: Path) -> Path:
    d = tmp_path / "Mesh"
    (d / "constant" / "triSurface").mkdir(parents=True)
    (d / "system").mkdir(parents=True)
    return d


def test_a_cell_straddling_the_plane_is_a_defect(case: Path):
    """Planes 1.7 and 1.9 bound ONE cell, and the surface at 1.8 is inside it."""
    _quad_stl(case / "constant/triSurface/merged_server_1U.stl", "Slot32_top", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_server_1U", ["Slot32_top"])
    _mesh(case / "constant/polyMesh", [1.6, 1.7, 1.9, 2.0], ["backgroundBoundary"])

    r = check_patch_solid_correspondence(case, "straddled")
    assert r.unresolved_surfaces == ["Slot32_top"], r.summary()
    assert r.sealed_voids == []
    assert not r.ok, "an unresolved surface must fail the gate"


def test_faces_only_on_one_side_is_buried(case: Path):
    """CMI-Guichou's ACU_A1_Left: faces stop short because a rack frame is
    flush against the ACU. Solid both sides, so no fluid face can exist."""
    _quad_stl(case / "constant/triSurface/merged_acu.stl", "ACU_A1_Left", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_acu", ["ACU_A1_Left"])
    _mesh(case / "constant/polyMesh", [1.6, 1.65, 1.7, 1.75], ["backgroundBoundary"])

    r = check_patch_solid_correspondence(case, "flush")
    assert r.sealed_voids == ["ACU_A1_Left"], r.summary()
    assert r.unresolved_surfaces == []
    assert r.ok, "a correctly-absent surface must not fail the gate"


def test_no_faces_anywhere_near_is_buried(case: Path):
    _quad_stl(case / "constant/triSurface/merged_acu.stl", "ACU_A1_Left", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_acu", ["ACU_A1_Left"])
    _mesh(case / "constant/polyMesh", [40.0, 40.1], ["backgroundBoundary"])

    r = check_patch_solid_correspondence(case, "isolated")
    assert r.sealed_voids == ["ACU_A1_Left"]
    assert r.unresolved_surfaces == []


def test_a_face_on_the_plane_means_the_patch_really_is_missing(case: Path):
    """The face exists, so it went to some OTHER patch. That is a real defect
    and must not be excused by either informational class."""
    _quad_stl(case / "constant/triSurface/merged_acu.stl", "ACU_A1_Left", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_acu", ["ACU_A1_Left"])
    _mesh(case / "constant/polyMesh", [1.7, 1.8, 1.9], ["backgroundBoundary"])

    r = check_patch_solid_correspondence(case, "mis-attributed")
    assert r.missing_patches == ["ACU_A1_Left"], r.summary()
    assert r.sealed_voids == [] and r.unresolved_surfaces == []
    assert not r.ok


def test_region_with_its_own_patch_is_never_examined(case: Path):
    _quad_stl(case / "constant/triSurface/merged_acu.stl", "ACU_A1_Left", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_acu", ["ACU_A1_Left"])
    _mesh(case / "constant/polyMesh", [1.6], ["ACU_A1_Left"])

    r = check_patch_solid_correspondence(case, "present")
    assert r.ok, r.summary()
    assert r.sealed_voids == [] and r.unresolved_surfaces == []


def _mesh_from_cells(poly: Path, cells: list, patches: list) -> None:
    """Build a mesh from explicit CELL intervals along x.

    `_mesh` writes a contiguous column, so any two consecutive face planes
    always bound a cell — which cannot express the shape that actually occurs:
    faces on both sides of a plane with the space between them carved away.
    Passing intervals makes the gap explicit: cells [(1.6,1.7),(1.9,2.0)] leave
    1.7..1.9 empty, so a surface at 1.8 has faces either side and no cell.
    Every face here is a boundary face of its own cell, which is exactly what a
    carved neighbourhood looks like.
    """
    poly.mkdir(parents=True, exist_ok=True)
    pts, faces, owner = [], [], []
    for c, (x0, x1) in enumerate(cells):
        for x in (x0, x1):
            base = len(pts)
            pts += [(x, 0.0, 0.0), (x, 1.0, 0.0), (x, 1.0, 1.0), (x, 0.0, 1.0)]
            faces.append((base, base + 1, base + 2, base + 3))
            owner.append(c)

    def _header(cls, obj, note=""):
        note_line = f'    note        "{note}";\n' if note else ""
        return (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            f"{note_line}    class {cls};\n    object {obj};\n}}\n// * * * //\n\n"
        )

    (poly / "points").write_text(
        _header("vectorField", "points") + f"{len(pts)}\n(\n"
        + "".join(f"({p[0]} {p[1]} {p[2]})\n" for p in pts) + ")\n",
        encoding="utf-8",
    )
    (poly / "faces").write_text(
        _header("faceList", "faces") + f"{len(faces)}\n(\n"
        + "".join("4(%d %d %d %d)\n" % f for f in faces) + ")\n",
        encoding="utf-8",
    )
    note = f"nPoints:{len(pts)}  nCells:{len(cells)}  nFaces:{len(faces)}  nInternalFaces:0"
    (poly / "owner").write_text(
        _header("labelList", "owner", note) + f"{len(owner)}\n(\n"
        + "".join(f"{v}\n" for v in owner) + ")\n",
        encoding="utf-8",
    )
    (poly / "neighbour").write_text(
        _header("labelList", "neighbour") + "0\n(\n)\n", encoding="utf-8"
    )
    entries = "".join(
        f"    {p}\n    {{\n        type            wall;\n"
        f"        nFaces          1;\n        startFace       0;\n    }}\n"
        for p in patches
    )
    (poly / "boundary").write_text(
        "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
        "    class polyBoundaryMesh;\n    object boundary;\n}\n"
        f"\n{len(patches)}\n(\n{entries})\n",
        encoding="utf-8",
    )


def test_carved_gap_between_faces_is_buried(case: Path):
    """Base2-9's real shape. Faces exist on both sides of the surface, but the
    space between them holds no cell — it was carved away. Nothing straddles the
    surface, and no grid line can put a face in carved space, so this is buried,
    not a defect."""
    _quad_stl(case / "constant/triSurface/merged_server_1U.stl", "U45_top", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_server_1U", ["U45_top"])
    _mesh_from_cells(
        case / "constant/polyMesh",
        [(1.6, 1.7), (1.9, 2.0)],   # 1.7 .. 1.9 is empty
        ["backgroundBoundary"],
    )

    r = check_patch_solid_correspondence(case, "carved-band")
    assert r.sealed_voids == ["U45_top"], r.summary()
    assert r.unresolved_surfaces == []
    assert r.ok


def test_one_cell_spanning_the_surface_is_a_defect(case: Path):
    """The same face planes, but now they bound a single cell. That cell
    straddles the surface, so the grid genuinely failed to resolve it."""
    _quad_stl(case / "constant/triSurface/merged_server_1U.stl", "U45_top", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_server_1U", ["U45_top"])
    _mesh_from_cells(
        case / "constant/polyMesh",
        [(1.7, 1.9)],               # one cell, the surface is inside it
        ["backgroundBoundary"],
    )

    r = check_patch_solid_correspondence(case, "straddling-cell")
    assert r.unresolved_surfaces == ["U45_top"], r.summary()
    assert r.sealed_voids == []
    assert not r.ok


def _empty_solid_stl(path: Path, name: str) -> None:
    """`solid X` immediately followed by `endsolid X` — no triangles at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"solid {name}\nendsolid {name}\n", encoding="utf-8")


def test_empty_stl_solid_is_its_own_class(case: Path):
    """Polygon_Room ships five of these in merged_server_5U.stl. The pipeline
    still declares a patch, so the boundary condition has no geometry anywhere.
    An EXPORT defect — named separately so it is not read as a meshing one."""
    _empty_solid_stl(
        case / "constant/triSurface/merged_server_5U.stl", "fan_server_1_top"
    )
    _snappy(case / "system/snappyHexMeshDict", "merged_server_5U", ["fan_server_1_top"])
    _mesh_from_cells(case / "constant/polyMesh", [(0.0, 1.0)], ["backgroundBoundary"])

    r = check_patch_solid_correspondence(case, "empty-stl")
    assert r.empty_solids == ["fan_server_1_top"], r.summary()
    assert r.missing_patches == []
    assert r.sealed_voids == [] and r.unresolved_surfaces == []
    assert not r.ok, "an empty solid is a real defect and must fail the gate"


def test_a_solid_with_geometry_is_not_called_empty(case: Path):
    _quad_stl(case / "constant/triSurface/merged_acu.stl", "ACU_A1_Left", 1.8)
    _snappy(case / "system/snappyHexMeshDict", "merged_acu", ["ACU_A1_Left"])
    _mesh_from_cells(case / "constant/polyMesh", [(1.6, 1.7)], ["backgroundBoundary"])

    r = check_patch_solid_correspondence(case, "has-geometry")
    assert r.empty_solids == []
