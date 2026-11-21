"""Geometry/source identities, not production solver acceptance."""
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_thin_source_migration as m

EVIDENCE=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/thin-source-d-migration-independent"

def extrusion(outline,height=2):
    outline=np.asarray(outline,dtype=float);n=len(outline)
    points=np.r_[np.c_[outline,np.zeros(n)],np.c_[outline,np.full(n,height)]]
    polys=[list(reversed(range(n))),list(range(n,2*n))]
    polys += [[j,(j+1)%n,(j+1)%n+n,j+n] for j in range(n)]
    return points,polys

def trace(points,polys,patch=0,zone=-1):
    n=len(polys)
    return m.source_index(dict(
        sourceManifest={"cells":1},points=np.asarray(points),owner=np.zeros(n,dtype=int),
        neighbour=np.full(n,-1,dtype=int),faceAlive=np.ones(n,dtype=int),
        cellAlive=np.ones(1,dtype=int),patch=np.full(n,patch),zone=np.full(n,zone),
        offsets=np.r_[0,np.cumsum(list(map(len,polys)))],
        vertices=np.asarray([v for f in polys for v in f]),volume=np.array([2.]),
        apex=np.array([[.5,.5,1.]])))

def test_oriented_cycle_ignores_rotation_not_reversal_or_one_ulp():
    p=np.array([[0.,0,0],[1,0,0],[1,1,0],[0,1,0]])
    assert m.cycle(p)==m.cycle(np.roll(p,2,axis=0))
    assert m.cycle(p)!=m.cycle(p[::-1])
    moved=p.copy();moved[1,0]=np.nextafter(1.,2.)
    assert m.cycle(p)!=m.cycle(moved)

def test_complete_shell_signature_is_order_independent_and_role_sensitive():
    p,f=extrusion([[0,0],[1,0],[1,1],[0,1]])
    a=m.parent_record(trace(p,f),0)["signature"]
    assert a==m.parent_record(trace(p,f[::-1]),0)["signature"]
    assert a!=m.parent_record(trace(p,f,patch=1),0)["signature"]
    assert a!=m.parent_record(trace(p,f,zone=1),0)["signature"]

def test_shell_signature_changes_with_tiling_or_physical_coordinate():
    p,f=extrusion([[0,0],[1,0],[1,1],[0,1]])
    a=m.parent_record(trace(p,f),0)["signature"]
    split=f[1:]+[[f[0][0],f[0][1],f[0][2]],[f[0][0],f[0][2],f[0][3]]]
    assert a!=m.parent_record(trace(p,split),0)["signature"]
    q=p.copy();q[0,0]=np.nextafter(0.,1.)
    assert a!=m.parent_record(trace(q,f),0)["signature"]

def test_patch_namespace_uses_actual_source_ids_not_compacted_patch_indices():
    t={"patch":np.array([401]),"faceBase":np.array([0])}
    class Mesh:
        patches=[(0,1,"realWall","wall")]
    assert m.realized_patch_roles(Mesh(),t)=={401:("realWall","wall")}

def test_namespace_collision_is_refused():
    t={"patch":np.array([401,401]),"faceBase":np.array([0,1])}
    class Mesh:
        patches=[(0,1,"wallA","wall"),(1,1,"wallB","wall")]
    with pytest.raises(ValueError,match="ambiguous"):
        m.realized_patch_roles(Mesh(),t)

def test_sourcebody_tensor_respects_selected_physical_and_coupled_mask():
    sf=np.array([[1.,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]])
    t={"patch":np.full(6,-1)}
    assert m.body_selected(t,np.arange(6),sf,{})==pytest.approx((1.,6))
    t["patch"][5]=401
    d,count=m.body_selected(t,np.arange(6),sf,{401:"wall"})
    assert count==5 and d==pytest.approx(.5)
    assert m.body_selected(t,np.arange(6),sf,{401:"processor"})==pytest.approx((1.,6))

def test_subfacet_requires_actual_role_and_original_outline_support():
    points=np.array([[0.,0,0],[1,0,0],[1,1,0],[0,1,0]])
    small=np.array([[.2,.2,0],[.4,.2,0],[.4,.4,0],[.2,.4,0]])
    args=([0],points,[[0,1,2,3]],[2],[3],np.array([[.5,.5,0]]),np.array([[0.,0,1.]]),1e-12)
    assert m.facet_match(small,2,3,*args)==0
    with pytest.raises(ValueError):m.facet_match(small,9,3,*args)
    with pytest.raises(ValueError):m.facet_match(small+np.array([1.,0,0]),2,3,*args)

def test_concave_axis_prism_and_moments_without_mutating_original_points():
    p,f=extrusion([[0,0],[2,0],[2,1],[1,1],[1,2],[0,2]],height=.3)
    old=p.copy();got=m.classify_prism(p,f)
    assert got["exactCoordinateAxisPrism"] and not got["convexOutline"]
    assert got["axis"]==2 and got["capArea"]==pytest.approx(3)
    assert got["volumeRelativeError"]<1e-10
    assert got["momentRelativeScaleError"]<1e-10
    assert np.array_equal(p,old)

@pytest.mark.parametrize("permutation",[[0,1,2],[2,0,1],[1,2,0]])
def test_coordinate_axis_choice_is_geometric(permutation):
    p,f=extrusion([[0,0],[2,0],[0,1]],height=.2)
    got=m.classify_prism(p[:,permutation],f)
    assert got["exactCoordinateAxisPrism"] and got["convexOutline"]
    assert got["axis"]==permutation.index(2)

def test_collinear_cap_nodes_are_retained_in_original_data():
    p,f=extrusion([[0,0],[.5,0],[1,0],[0,1]])
    old=p.copy();got=m.classify_prism(p,f)
    assert got["exactCoordinateAxisPrism"] and got["capOutlineVertices"]==3
    assert got["originalCapNodeCounts"]==[4,4]
    assert len(got["sourceVertices"])==8 and np.array_equal(p,old)

def test_open_shell_is_unknown_even_when_bbox_looks_like_box():
    p,f=extrusion([[0,0],[1,0],[1,1],[0,1]])
    got=m.classify_prism(p,f[:-1])
    assert not got["exactCoordinateAxisPrism"]
    assert got["attempts"][0]["reason"]=="nonFiniteOrNotPositiveDirectedClosedShell"

def test_near_prism_one_ulp_offset_is_not_silently_snapped():
    p,f=extrusion([[0,0],[1,0],[1,1],[0,1]])
    p[4,0]=np.nextafter(0.,1.)
    assert not m.classify_prism(p,f)["exactCoordinateAxisPrism"]

def test_simple_outline_refuses_nonadjacent_touch_and_self_intersection():
    F=m.Fraction
    bow=[(F(0),F(0)),(F(1),F(1)),(F(0),F(1)),(F(1),F(0))]
    assert not m.simple(bow)
    touch=[(F(0),F(0)),(F(2),F(0)),(F(1),F(1)),(F(1),F(0)),(F(0),F(1))]
    assert not m.simple(touch)

@pytest.mark.parametrize("kind",["concave","convex","unsupported"])
def test_actual_frozen_source_prism_fixtures(kind):
    doc=json.loads((EVIDENCE/f"compact-prism-{kind}.json").read_text())
    got=m.classify_prism(doc["points"],doc["polys"])
    expected=doc["expected"]
    assert got["exactCoordinateAxisPrism"]==expected["exactCoordinateAxisPrism"]
    if expected["exactCoordinateAxisPrism"]:
        assert got["convexOutline"]==expected["convexOutline"]
        assert got["axis"]==expected["axis"]
        assert got["volumeRelativeError"]<1e-10
    else:
        assert [v["reason"] for v in got["attempts"]]==[v["reason"] for v in expected["attempts"]]
