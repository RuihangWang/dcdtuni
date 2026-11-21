"""Triangle ideal is a single-base optimum, while the whole-parent veto remains real."""
from pathlib import Path
import importlib.util,json,math
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
S=importlib.util.spec_from_file_location("ideal",ROOT/"tools/audit_air_triangle_ideal_apex.py")
M=importlib.util.module_from_spec(S);S.loader.exec_module(M)
S=importlib.util.spec_from_file_location("bound",ROOT/"tools/audit_air_pyramid_remaining_bound.py")
B=importlib.util.module_from_spec(S);S.loader.exec_module(B)
E=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-triangle-ideal-independent"

@pytest.mark.parametrize("selected",[3,4])
@pytest.mark.parametrize("base",[
 [[0,0,0],[1,0,0],[.25,.5,0]],
 [[0,0,0],[2,0,0],[.1,.01,0]],
 [[1,2,3],[2,2,4],[1.3,2.5,3.3]]])
def test_scalene_tilted_triangle_ideal_attains_global_minkowski_bound(selected,base):
    p=np.array(base,float);normal=np.cross(p[1]-p[0],p[2]-p[0]);old=p.mean(0)+normal
    row=M.ideal_triangle_apex(p,old,selected)
    actual=B.raw_formula(p,row["idealApex"],selected==3)["predictedDeterminant"]
    upper=M.B.fixed_triangle_bound(p,selected)["bound"]
    assert actual==pytest.approx(upper,rel=1e-11,abs=1e-13)
    assert row["idealHeightM"]==pytest.approx(math.sqrt(2 if selected==3 else 8)*row["inradiusM"])

def test_plane_clip_uses_original_halfspaces_without_overwriting_time_or_geometry():
    planes=[{"sourceFace":1,"normal":[1,0,0],"centre":[1,0,0]},
            {"sourceFace":2,"normal":[-1,0,0],"centre":[0,0,0]}]
    row=M.clipped_kernel_segment([.5,0,0],[2,0,0],planes)
    assert row["limitingOriginalFaces"]==[1]
    assert row["clippedTarget"][0]<1-3e-6
    assert row["clippedMinimumOriginalPlaneMarginM"]>=3e-6
    with pytest.raises(ValueError):M.clipped_kernel_segment([1,0,0],[2,0,0],planes)

def test_nonfinite_degenerate_or_unsupported_triangle_branch_is_refused():
    with pytest.raises(ValueError):M.ideal_triangle_apex([[0,0,0],[1,0,0],[2,0,0]],[0,0,1],3)
    with pytest.raises(ValueError):M.ideal_triangle_apex([[0,0,0],[1,0,0],[0,1,float("inf")]],[0,0,1],3)
    with pytest.raises(ValueError):M.ideal_triangle_apex([[0,0,0],[1,0,0],[0,1,0]],[0,0,1],5)

def test_actual_fewer_bad_labels_still_rejects_worsening_nonorthogonality_severity():
    fixture=next(x for x in json.loads((E/"actual-stencils.json").read_text()) if x["stencil"]["rank"]==4 and x["stencil"]["parent"]==26710)
    raw=next(x for x in json.loads((E/"actual-proposals.json").read_text())["results"] if x["rank"]==4 and x["parent"]==26710)
    doc=fixture["stencil"];old=M.Q.actual_stencil_quality(doc,doc["originalApex"])
    new=M.Q.actual_stencil_quality(doc,raw["trials"][0]["point"]);p=M.Q.actual_pareto(old,new)
    assert len(p["determinant"]["candidateBad"])<len(p["determinant"]["oldBad"])
    assert all(x["noNewFailures"] for x in p.values())
    assert not p["nonOrthogonality"]["worstFailedSeverityDoesNotIncrease"]

def test_actual_accepted_small_blend_is_partial_and_preserves_parent_moments():
    fixture=next(x for x in json.loads((E/"actual-stencils.json").read_text()) if x["stencil"]["rank"]==3 and x["stencil"]["parent"]==10906)
    raw=next(x for x in json.loads((E/"actual-proposals.json").read_text())["results"] if x["rank"]==3 and x["parent"]==10906)
    doc=fixture["stencil"];old=M.Q.actual_stencil_quality(doc,doc["originalApex"])
    new=M.Q.actual_stencil_quality(doc,raw["trials"][0]["point"]);p=M.Q.actual_pareto(old,new)
    assert all(x["noNewFailures"] and x["countDoesNotIncrease"] and x["worstFailedSeverityDoesNotIncrease"] for x in p.values())
    assert len(p["determinant"]["oldBad"])==len(p["determinant"]["candidateBad"])==4
    assert new["minimumDeterminant"]>old["minimumDeterminant"]
    assert new["sumVolume"]==pytest.approx(old["sumVolume"],rel=1e-10)
    assert new["firstMoment"]==pytest.approx(old["firstMoment"],rel=1e-10)
