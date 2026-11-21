"""Meaningful read-only face partition geometry regressions."""
import importlib.util
import sys
from pathlib import Path
import numpy as np
import pytest
path=Path(__file__).resolve().parents[3]/"tools/audit_air_face_partition.py"
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location("face_partition",path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def polygon():
    return np.array([(0,0,1),(2,0,1),(2,1,1),(1,1,1),(1,2,1),(.5,2,1),(0,2,1)],float)
def test_ears_preserve_outline_collinear_point_and_are_reversal_invariant():
    pts=polygon();pieces=m.ear_partition(pts);rev=m.ear_partition(pts[::-1])
    assert len(pieces)==len(pts)-2
    check=m.validate_partition(pts,pieces)
    assert check["allOriginalVerticesReferenced"] and check["allNewDiagonalEdgesPairedOppositely"]
    assert sorted(m.canonical(p,pts) for p in pieces)==sorted(m.canonical(p,pts[::-1]) for p in rev)
    assert all(m.convex(pts[list(p)]) for p in pieces)
    assert min(m.fan_quality(pts[list(p)],np.array([.7,.7,0]))["minimumQuality"] for p in pieces)>0
def test_convex_merge_retains_collinear_outline_and_good_fans():
    pts=polygon();own=np.array([.7,.7,0]);pieces=m.convex_partition(pts,own)
    assert len(pieces)<len(pts)-2
    assert m.validate_partition(pts,pieces)["allOriginalVerticesReferenced"]
    assert all(m.convex(pts[list(p)]) for p in pieces)
    assert all(m.fan_quality(pts[list(p)],own)["minimumQuality"]>0 for p in pieces)
@pytest.mark.parametrize("points",[
    [(0,0,0),(2,2,0),(0,2,0),(2,0,0)],
    [(0,0,0),(3,3,0),(0,3,0),(2,0,0)],
    [(0,0,0),(2,0,0),(0,0,0),(0,2,0)],
    [(0,0,0),(2,0,0),(2,2,.1),(0,2,0)],
])
def test_refuses_non_simple_or_non_planar_input(points):
    with pytest.raises(ValueError):m.ear_partition(points)
def test_triangle_partition_adds_strict_coplanar_cell_trigger():
    pts=np.array([(0,0,1),(1,0,1),(1,1,1),(0,1,1),(0,0,0),(1,0,0),(1,1,0),(0,1,0)],float)
    faces=[(0,1,2,3),(7,6,5,4),(4,5,1,0),(5,6,2,1),(6,7,3,2),(7,4,0,3)]
    original=m.Geometry.from_arrays(pts,faces).classify(0)
    split=m.Geometry.from_arrays(pts,[*m.ear_partition(pts[:4]),*faces[1:]]).classify(0)
    assert sum(original["checkerTriggerCounts"].values())==0
    assert split["checkerTriggerCounts"]["coplanar"]==2
    assert abs(split["volumeM3"]-original["volumeM3"])<1e-14

def test_rotating_polygon_start_preserves_same_ear_and_convex_piece_geometry():
    pts=polygon();own=np.array([.7,.7,0])
    for algorithm in [m.ear_partition,lambda p:m.convex_partition(p,own)]:
        reference=sorted(m.canonical(p,pts) for p in algorithm(pts))
        for shift in range(len(pts)):
            moved=np.roll(pts,shift,axis=0)
            assert sorted(m.canonical(p,moved) for p in algorithm(moved))==reference
