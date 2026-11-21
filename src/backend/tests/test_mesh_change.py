"""mesh_change: what an STL edit asks of the existing mesh, and the boundary-face reassignment of a patch update."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.aircooling.runner import mesh_change as mc
from backend.tests.test_scano_ai import LATTICE, _box_faces, reconstructed_case


def _quad_tris(quad):
    a, b, c, d = (np.array(LATTICE[i], float) for i in quad)
    return [(a, b, c), (a, c, d)]


def _stl(solids: dict) -> bytes:
    out = []
    for name, tris in solids.items():
        out.append(f"solid {name}")
        for t in tris:
            out.append("  facet normal 0 0 0\n    outer loop")
            out += [f"      vertex {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}" for p in t]
            out.append("    endloop\n  endfacet")
        out.append(f"endsolid {name}")
    return ("\n".join(out) + "\n").encode()


def _walls(skip=()):
    c0, c1 = _box_faces(0), _box_faces(1)
    quads = [c0[s] for s in ("y-", "y+", "z-", "z+")] + [c1[s] for s in ("y-", "y+", "z-", "z+")]
    return [t for k, q in enumerate(quads) if k not in skip for t in _quad_tris(q)]


def _write(tri: Path, solids: dict) -> dict:
    tri.mkdir(parents=True, exist_ok=True)
    data = _stl(solids)
    (tri / "room.stl").write_bytes(data)
    return {"room.stl": data}


def test_parse_stl_and_surface_test():
    solids = mc.parse_stl(_stl({"walls": _walls(), "inlet": _quad_tris(_box_faces(0)["x-"])}))
    assert set(solids) == {"walls", "inlet"} and solids["walls"].shape == (16, 3, 3)
    assert mc.same_surface(solids["walls"], solids["walls"][::-1], 1e-6)
    moved = solids["walls"].copy()
    moved[:, :, 0] += 0.25
    assert not mc.same_surface(solids["walls"], moved, 1e-6)


def test_classify_none_patches_geometry_mesh(tmp_path):
    tri = tmp_path / "tri"
    base = {"walls": _walls(), "inlet": _quad_tris(_box_faces(0)["x-"])}
    _write(tri, base)
    assert mc.classify(tri, {"room.stl": _stl(base)}, True)["kind"] == "none"
    assert mc.classify(tri, {"room.stl": _stl(base)}, False)["kind"] == "mesh"
    # cube 1's y- face opened as its own patch: the same surface, divided differently
    split = {"walls": _walls(skip=(4,)), "inlet": base["inlet"], "opening": _quad_tris(_box_faces(1)["y-"])}
    r = mc.classify(tri, {"room.stl": _stl(split)}, True)
    assert r["kind"] == "patches" and r["reassign_from"] == ["walls"] and r["reassign_to"] == ["opening", "walls"]
    # the opening pushed out of the wall: the surface changed
    out = [tuple(p + np.array([0, -0.2, 0]) for p in t) for t in split["opening"]]
    r = mc.classify(tri, {"room.stl": _stl({**split, "opening": out})}, True)
    assert r["kind"] == "geometry"


def test_boundary_reassignment_moves_only_the_opened_faces(tmp_path):
    case = reconstructed_case(tmp_path / "case")
    poly = case / "constant" / "polyMesh"
    targets = {"walls": np.array(_walls(skip=(4,))), "opening": np.array(_quad_tris(_box_faces(1)["y-"]))}
    moves = mc.boundary_reassignment(poly, ["walls"], targets)
    assert {k: v.tolist() for k, v in moves.items()} == {"opening": [7]}     # walls start at 3: c0 y-,y+,z-,z+, c1 y-
    with pytest.raises(ValueError, match="off every new patch"):
        far = {"opening": targets["opening"] + np.array([0, -1.0, 0])}
        mc.boundary_reassignment(poly, ["walls"], far)


def test_create_patch_dict_and_region_types(tmp_path):
    snappy = tmp_path / "snappyHexMeshDict"
    snappy.write_text("regions\n{\n    acu_Front_patch\n    {\n        level (2 2);\n        patchInfo { type patch; }\n"
                      "    }\n    acu_Left { level (2 2); patchInfo { type wall; inGroups (wall); } }\n}\n")
    kinds = mc.region_patch_types(snappy, ["acu_Front_patch", "acu_Left", "unknown"])
    assert kinds == {"acu_Front_patch": "patch", "acu_Left": "wall", "unknown": "wall"}
    text = mc.create_patch_dict([("acu_Front_patch", "s0", "patch"), ("acu_Left", "s1", "wall")])
    assert "{ name acu_Front_patch; patchInfo { type patch; } constructFrom set; set s0; }" in text
    assert "patchInfo { type wall; inGroups (wall); }" in text
    mc.write_face_set(tmp_path / "sets" / "s0", "s0", np.array([4, 9]))
    assert "2\n(\n4\n9\n)" in (tmp_path / "sets" / "s0").read_text()


def test_role_changes_and_retype(tmp_path):
    """A role change between the mesh's manifest and the current one is reported; retype_boundary sets the type
    and keeps every face where it was."""
    old = {"boundaryBindings": {"a": {"patchName": "inlet", "bcType": "acu_supply"},
                                "b": {"patchName": "outlet", "bcType": "fan_outlet"}}}
    new = {"boundaryBindings": {"a": {"patchName": "inlet", "bcType": "wall"},
                                "b": {"patchName": "outlet", "bcType": "fan_outlet"},
                                "c": {"patchName": "walls", "bcType": "acu_return"}}}
    assert mc.role_changes(old, new) == {"inlet": ["acu_supply", "wall"], "walls": [None, "acu_return"]}
    case = reconstructed_case(tmp_path / "case")
    boundary = case / "constant" / "polyMesh" / "boundary"
    (tmp_path / "before").write_text(boundary.read_text())
    assert mc.retype_boundary(boundary, {"inlet": "wall", "walls": "patch"}) == 2
    from backend.aircooling.scano.foam_mesh import parse_boundary
    after = dict(parse_boundary(boundary))
    assert after["inlet"]["type"] == "wall" and after["inlet"]["inGroups"] == "1(wall)"
    assert after["walls"]["type"] == "patch" and "inGroups" not in after["walls"]
    assert after["outlet"]["type"] == "patch"
    assert [(n, d["nFaces"], d["startFace"]) for n, d in parse_boundary(boundary)] == \
        [(n, d["nFaces"], d["startFace"]) for n, d in parse_boundary(tmp_path / "before")]


def test_mesh_settings_changes_need_a_new_mesh():
    """A cell size, refinement level or engine change is reported (the surface is the same, the mesh is not);
    how the mesher ran and the editor's view switches are not, nor a key only one manifest carries."""
    old = {"meshConfig": {"meshBaseSize": 0.34, "refinementLevel": [1, 1], "meshProcess": 24,
                          "locInMeshVisible": False, "blockMeshEditActive": False},
           "meshGeometry": {"meshConfig": {"baseSize": 0.34, "meshEngine": "snappy"}}}
    same = {"meshConfig": {"meshBaseSize": 0.34, "refinementLevel": [1, 1], "meshProcess": 8,
                           "locInMeshVisible": True, "blockMeshEditActive": True, "weldTolerance": 0.0012},
            "meshGeometry": {"meshConfig": {"baseSize": 0.34, "meshEngine": "snappy", "parallelMesh": False}}}
    assert mc.mesh_settings_changes(old, same) == []
    finer = {"meshConfig": {"meshBaseSize": 0.2, "refinementLevel": [1, 2]},
             "meshGeometry": {"meshConfig": {"baseSize": 0.2, "meshEngine": "dcCart"}}}
    assert mc.mesh_settings_changes(old, finer) == ["baseSize", "meshBaseSize", "meshEngine", "refinementLevel"]
    assert mc.mesh_settings_changes({}, finer) == []
