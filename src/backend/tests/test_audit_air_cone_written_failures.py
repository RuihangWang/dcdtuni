"""Physical written-hole and coupled indexing regressions; no native execution."""
import importlib.util
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("cyclic",ROOT/"tools/audit_air_cyclic_written_order.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_exact_open_tetrahedron_hole_is_not_short_edge_point_loss():
    e=np.finfo(float).eps
    points=[[0.,0.,0.],[0.,0.,1.],[0.,e,.25],[1.,0.,.5]]
    mesh=m.g.Geometry.from_arrays(points,[[0,1,3],[1,2,3],[2,0,3]])
    r=m.h.audit_cell(mesh,0)
    assert r["openTetrahedronPattern"]
    hole=r["closedHoleCycles"][0]
    assert hole["exactAreaM2"]==e/2
    assert hole["writerWouldDropAtArea1e-12"]
    assert r["minimumWrittenPointPairDistanceM"]>.1
    assert hole["allHolePointIdsRemainInWrittenFaces"]

def test_zero_area_missing_base_is_reported_instead_of_claimed_valid_face():
    mesh=m.g.Geometry.from_arrays([[0.,0.,0.],[0.,0.,1.],[0.,0.,.25],[1.,0.,.5]],
                                  [[0,1,3],[1,2,3],[2,0,3]])
    r=m.h.audit_cell(mesh,0)
    assert r["closedHoleCycles"][0]["exactAreaM2"]==0
    assert r["closedHoleCycles"][0]["writerWouldDropAtArea1e-12"]

def test_positive_area_hole_does_not_blame_existing_writer_floor():
    mesh=m.g.Geometry.from_arrays([[0.,0.,0.],[0.,0.,1.],[0.,1.,.25],[1.,0.,.5]],
                                  [[0,1,3],[1,2,3],[2,0,3]])
    r=m.h.audit_cell(mesh,0)
    assert r["openTetrahedronPattern"]
    assert not r["closedHoleCycles"][0]["writerWouldDropAtArea1e-12"]

def test_same_geometric_triangle_whole_reversal_has_wrong_cyclic_point_map():
    master=[774,1236,1237];bad=master[::-1]
    assert m.canonical_ids(master)==m.canonical_ids(bad)
    assert m.point_pair_map(master,bad)==[(774,1237),(1236,774),(1237,1236)]
    good=m.same_start_reverse(master)
    assert m.point_pair_map(master,good)==[(774,774),(1236,1236),(1237,1237)]

def test_same_start_reversal_keeps_each_quad_point_correspondence():
    master=[3,8,12,19]
    assert m.point_pair_map(master,m.same_start_reverse(master))==[(x,x) for x in master]

def test_fuzzy_centroid_comparator_has_a_real_order_cycle():
    def less(a,b):
        for x,y in zip(a,b):
            if x-y < -1e-6:return True
            if x-y > 1e-6:return False
        return False
    a=(0.,0.,0.);b=(.75e-6,-1.,0.);c=(1.5e-6,-2.,0.)
    assert less(a,c) and less(c,b) and less(b,a)
    assert sorted([a,b,c])==[a,b,c] # Exact tuple ordering is a strict total order here.
