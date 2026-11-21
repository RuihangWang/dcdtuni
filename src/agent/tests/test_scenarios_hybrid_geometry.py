"""Exported geometry for the zonal LF networks: STL solids by hash, areas, height slices, the server face ledger."""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from agent.scenarios.hybrid import geometry as G


def rectangle_x(name, x, y0, y1, z0, z1):
    """An ASCII STL solid: the rectangle x = const, y0..y1 by z0..z1 (two triangles)."""
    corners = [(x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1)]
    lines = [f"solid {name}"]
    for tri in ((0, 1, 2), (0, 2, 3)):
        lines += ["  facet normal 1 0 0", "    outer loop"]
        lines += ["      vertex %f %f %f" % corners[i] for i in tri]
        lines += ["    endloop", "  endfacet"]
    return "\n".join(lines + [f"endsolid {name}"]) + "\n"


def test_area_centre_and_height_slices_of_a_vertical_rectangle():
    (tri,) = G.parse_solids(rectangle_x("r", 1., .8, 2.8, 0., .5)).values()
    area, centre = G.area_and_centre(tri)
    assert area == pytest.approx(1.) and centre == pytest.approx([1., 1.8, .25])
    assert G.slice_area(tri, .8, 1.8) == pytest.approx(.5)
    assert G.slice_area(tri, 1.3, 2.3) == pytest.approx(.5)
    assert G.slice_area(tri, 2.8, 4.) == pytest.approx(0., abs=1e-12)
    assert G.slice_area(tri, 0., 9.) == pytest.approx(area)
    # Layer slices partition the solid.
    bounds = (.8, 1.5, 2.1, 2.8)
    assert sum(G.slice_area(tri, lo, hi) for lo, hi in zip(bounds, bounds[1:])) == pytest.approx(area)


def test_load_solids_reads_only_the_bound_bytes(tmp_path):
    text = rectangle_x("a", 0., 0., 1., 0., 1.) + rectangle_x("b", 1., 0., 1., 0., 2.)
    (tmp_path / "x.stl").write_text(text)
    solids = G.load_solids(tmp_path, {"x.stl": hashlib.sha256(text.encode()).hexdigest()})
    assert set(solids) == {"a", "b"} and solids["b"].shape == (2, 3, 3)
    with pytest.raises(ValueError, match="changed"):
        G.load_solids(tmp_path, {"x.stl": "0" * 64})
    (tmp_path / "y.stl").write_text(rectangle_x("a", 2., 0., 1., 0., 1.))
    both = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ("x.stl", "y.stl")}
    with pytest.raises(ValueError, match="ambiguous"):
        G.load_solids(tmp_path, both)
    with pytest.raises(ValueError, match="positive"):
        G.area_and_centre(np.zeros((1, 3, 3)))


def test_the_ledger_binds_each_server_intake_and_outlet_patch_solid():
    project = {"constructions": {"racks": {"m_1": {"constructions": {"servers": {
        "e_1": {"faces": {"outlet": {"patches": [{"id": "p_9"}]}}}}}}}}}
    path = "constructions.racks.m_1.servers.e_1"
    manifest = {"sourceProject": {"contentHash": "c" * 64}, "geometryExport": {"solids": {
        "0": {"objectPath": path, "surfaceUid": "inlet___face__", "solidName": "front"},
        "1": {"objectPath": path, "surfaceUid": "outlet_p_9", "solidName": "rear"},
        "2": {"objectPath": path, "surfaceUid": "outlet___wall__", "solidName": "wall"}}}}
    solids = {**G.parse_solids(rectangle_x("front", 0., .8, 1., 0., .4)),
              **G.parse_solids(rectangle_x("rear", 1.2, .8, 1., 0., .1))}
    ledger = G.geometry_ledger(project, manifest, solids)
    assert ledger["project_content_hash"] == "c" * 64 and ledger["physics_evaluation"] is False
    (row,) = ledger["servers"]
    assert row["server_uid"] == "m_1/e_1"
    assert [(f["face"], f["solid_name"]) for f in row["faces"]] == [("front", "front"), ("rear", "rear")]
    assert [f["area_m2"] for f in row["faces"]] == pytest.approx([.08, .02])
    project["constructions"]["racks"]["m_1"]["constructions"]["servers"]["e_1"]["faces"]["outlet"]["patches"] = [
        {"id": "p_8"}]
    with pytest.raises(ValueError, match="outlet patch"):
        G.geometry_ledger(project, manifest, solids)
