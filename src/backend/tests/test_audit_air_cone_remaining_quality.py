"""Installed quality formula regression; no native solver or mesh execution."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("remaining",ROOT/"tools/audit_air_cone_remaining_quality.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_cube_six_internal_area_vectors_have_unit_determinant():
    a=np.vstack((np.eye(3),-np.eye(3)))
    d,e,t=m.determinant(a)
    assert d==pytest.approx(1.)
    assert e==pytest.approx([2.,2.,2.])
def test_legal_physical_faces_do_not_repair_rank_one_internal_stencil():
    physical=np.array([[0.,1,0],[0,0,1]])
    internal=np.array([[1.,0,0],[1.,0,0],[1.,0,0],[1.,0,0]])
    assert m.determinant(internal)[0]==0.
    assert m.determinant(np.vstack((internal,physical)))[0]>0.
def test_determinant_is_invariant_to_rotation_and_uniform_area_scale():
    a=np.array([[1.,2,3],[3,0,1],[1,4,0]])
    q,_=np.linalg.qr(np.array([[1.,2,4],[3,1,2],[2,4,1.]]))
    assert m.determinant(a)[0]==pytest.approx(m.determinant(a@q*7.2)[0])
def test_face_weight_depends_on_projected_cc_distances_not_tangential_offset():
    a=m.face_metrics([1.,0,0],[0.,0,0],[-.000054,5.,7.],1.,[[0,0,0],[0,1,0],[0,0,1]],[.0234,-3.,8.],10.)
    b=m.face_metrics([1.,0,0],[0.,0,0],[-.000054,0.,0.],1.,[[0,0,0],[0,1,0],[0,0,1]],[.0234,0.,0.],10.)
    assert a["weight"]==b["weight"]==pytest.approx(.000054/(.000054+.0234))
    assert a["volumeRatio"]==.1
def test_wall_skewness_detects_cc_projection_outside_small_triangle():
    points=[[0.,0,0],[.001837,0,0],[0,-.0055555,0]]
    fc=np.asarray(points).mean(axis=0);sf=np.cross(np.subtract(points[1],points[0]),np.subtract(points[2],points[0]))*.5
    centered=m.face_metrics(sf,fc,[fc[0],fc[1],.002],1.,points)
    outside=m.face_metrics(sf,fc,[.0082718,-.0100694,.002],1.,points)
    assert centered["skewness"]==pytest.approx(0.,abs=1e-15)
    assert outside["skewness"]>4.
def test_actual_sample_polygon_reconstructs_flagged_metrics():
    a=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-remaining-quality-samples.json"
    import json
    rows=json.loads(a.read_text())["samples"]
    for r in rows:
      if r["kind"]!="faceQualitySample":continue
      value=m.face_metrics(r["SF"],r["FC"],r["ownerCC"],r["ownerV"],r["pointCoordinates"],r["neighbourCC"],r["neighbourV"])
      assert value["skewness"]==pytest.approx(r["skewness"],rel=1e-13,abs=1e-15)
      for metric in ["weight","volumeRatio"]:
        if metric in r:assert value[metric]==pytest.approx(r[metric],rel=1e-13)

def test_rectangular_pyramid_distinguishes_near_base_from_skinny_base():
    w=.05;h=.015;epsilon=.0007
    geo=m.g.Geometry.from_arrays([[0,0,0],[0,w,0],[0,w,h],[0,0,h],[epsilon,w*.9,-h*3]],
        [[0,3,2,1],[0,1,4],[1,2,4],[2,3,4],[3,0,4]])
    r=m.base_apex_candidates(geo,0)
    assert r["selection"]=="uniqueQuadrilateralPyramidBase"
    base=r["candidates"][0]
    assert base["apexNormalHeightM"]==pytest.approx(epsilon)
    assert base["baseMinimumInteriorAngleDegrees"]==pytest.approx(90.)
    assert base["apexNormalHeightToBaseRadius"]<.1
    assert not base["apexProjectionInsideBase"]
    assert not r["originalParentIdentityAvailable"]
def test_all_internal_tetrahedron_does_not_invent_original_base_identity():
    geo=m.g.Geometry.from_arrays([[0,0,0],[.064,.016,0],[.0658,.016,0],[.032,-.01,.0077]],
        [[0,2,1],[0,1,3],[1,2,3],[2,0,3]])
    r=m.base_apex_candidates(geo,0)
    assert r["selection"]=="allTetrahedronBasePossibilitiesNoOriginalBaseIdentity"
    assert len(r["candidates"])==4
    base=next(v for v in r["candidates"] if v["baseFace"]==0)
    assert base["apexNormalHeightM"]==pytest.approx(.0077)
    assert base["baseMinimumInteriorAngleDegrees"]<1.
    assert base["baseMaximumEdgeToRadius"]>200.

def test_exact_fixed_skinny_triangle_bound_rejects_any_apex_only_repair():
    from fractions import Fraction
    p=np.array([[0.,0,0],[.064,.016,0],[.0658,.016,0]])
    bound=m.fixed_triangle_determinant_upper_bound(p)
    assert isinstance(bound,Fraction) and bound<Fraction(1,1000)
    rng=np.random.default_rng(8)
    for i in range(300):
        apex=np.array([rng.uniform(-.1,.1),rng.uniform(-.1,.1),10**rng.uniform(-6,1)])
        areas=[np.cross(p[(j+1)%3]-p[j],apex-p[j])*.5 for j in range(3)]
        actual=m.determinant(areas)[0]
        assert actual<=float(bound)*(1+1e-10)+1e-15
    assert m.fixed_triangle_determinant_upper_bound(p*8)==bound
def test_determinant_identity_for_three_side_areas_matches_installed_definition():
    p=np.array([[0.,0,0],[2.,0,0],[.7,.3,0]])
    apex=np.array([.3,-.1,.12])
    areas=np.array([np.cross(p[(j+1)%3]-p[j],apex-p[j])*.5 for j in range(3)])
    A=np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0]))*.5;h=apex[2];S=np.linalg.norm(areas,axis=1).sum()
    assert m.determinant(areas)[0]==pytest.approx(729*A**4*h**4/(32*S**6),rel=1e-12)
