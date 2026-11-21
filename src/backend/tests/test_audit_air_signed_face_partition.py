"""Independent geometry failures and conservation identities, no native execution."""
import importlib.util
from fractions import Fraction as F
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("signed_partition",ROOT/"tools/audit_air_signed_face_partition.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def xyz(p,z=3.): return [[float(x),float(y),z] for x,y in p]
L=xyz([(0,0),(3,0),(3,1),(1,1),(1,3),(0,3)])
L_IDS=[[3,4,5],[3,5,0],[3,0,1],[3,1,2]]

def test_concave_outline_signed_centroid_is_independent_of_abs_triangle_weights():
    oracle=m.outline_oracle(L)
    assert oracle["projectedArea"]==5
    assert oracle["centre"]==(F(11,10),F(11,10),F(3))
    # Reproduce the absolute-triangle centre construction separately from shoelace.
    q=[tuple(map(m.rational,p[:2])) for p in L]
    anchor=tuple(sum(p[k] for p in q)/len(q) for k in range(2))
    weights=[abs(m.orient(anchor,a,b))/2 for a,b in m.edges(q)]
    centres=[tuple((anchor[k]+a[k]+b[k])/3 for k in range(2)) for a,b in m.edges(q)]
    old=tuple(sum(w*c[k] for w,c in zip(weights,centres))/sum(weights) for k in range(2))
    assert old!=(F(11,10),F(11,10))
    assert all(x==F(401,342) for x in old)
    assert m.validate_coverage(oracle,[[L[i] for i in p] for p in L_IDS])["exactSignedAreaAndFirstMomentsEqual"]

def test_nonadjacent_touch_is_rejected_without_proper_crossing():
    p=[tuple(map(F,q)) for q in [(0,0),(1,0),(-1,1),(-1,-1),(2,0),(-2,2)]]
    assert not any(m.proper_cross(a,b,c,d) for i,(a,b) in enumerate(list(m.edges(p)))
                   for j,(c,d) in enumerate(list(m.edges(p))) if j>i)
    with pytest.raises(ValueError,match="crossing/touch/overlap"):m.require_simple(p)

def test_contained_triangles_overlap_but_shared_diagonal_does_not():
    a=[(F(0),F(0)),(F(2),F(0)),(F(0),F(2))]
    b=[(F(0),F(0)),(F(1),F(0)),(F(0),F(1))]
    assert m.triangle_intersection_area(a,b)==F(1,2)
    c=[(F(2),F(0)),(F(2),F(2)),(F(0),F(2))]
    assert m.triangle_intersection_area(a,c)==0

def test_reversal_and_rotation_leave_independent_signed_centroid_invariant():
    base=m.outline_oracle(L)
    for p in [L[2:]+L[:2],L[::-1]]:
        o=m.outline_oracle(p)
        assert o["centre"]==base["centre"]
        assert abs(o["projectedArea"])==abs(base["projectedArea"])
    rev=m.outline_oracle(L[::-1])
    assert rev["area"]==tuple(-x for x in base["area"])

def test_moved_point_and_tiny_nonplanarity_are_not_silently_accepted():
    pieces=[[L[i][:] for i in p] for p in L_IDS]
    pieces[0][0][0]+=1e-8
    with pytest.raises(ValueError,match="adds/moves"):m.validate_coverage(m.outline_oracle(L),pieces)
    warped=[p[:] for p in L];warped[0][2]+=1e-14
    with pytest.raises(ValueError,match="exactly planar"):m.outline_oracle(warped)

def test_tangential_centroid_shift_preserves_normal_pyramid_volume():
    o=m.outline_oracle(L);centre=list(map(float,o["centre"]))
    row={"points":L,"pieces":[[L[i] for i in p] for p in L_IDS],
       "face":1,"owner":2,"neighbour":3,"patch":-1,"zone":-1,
       "oldOFcentre":[7/6,7/6,3.],"pieceCentre":centre,"signedCentre":centre,
       "ownerApex":[1.,1.,2.],"neighbourApex":[1.,1.,4.]}
    report=m.analyse(row)
    assert report["tangentialCentreShiftM"]>0
    assert report["normalCentreShiftM"]==0
    for p in report["pyramidVolumeComparison"].values():
        assert p["exactPartitionVolumeEqualsSignedOriginal"]
        assert p["oldOFRelativeVolumeDifference"]==0

def test_overlapping_positive_pieces_are_rejected():
    square=xyz([(0,0),(2,0),(2,2),(0,2)])
    # Two different complete triangulations overlap over the entire square.
    pieces=[[square[i] for i in p] for p in [[0,1,2],[0,2,3],[0,1,3],[1,2,3]]]
    with pytest.raises(ValueError):m.validate_coverage(m.outline_oracle(square),pieces)
