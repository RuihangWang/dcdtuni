"""Pure math regression: stock W/R branch, exact exclusions and frozen proposals."""
import importlib.util
import json
from fractions import Fraction as F
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
SPEC=importlib.util.spec_from_file_location("cone_quality",ROOT/"tools/audit_air_cone_quality_kernel.py")
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
EVIDENCE=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-quality-kernel"

@pytest.mark.parametrize("apex",[[.2,.3,.4],[2.,-3.,5.],[-4.,-2.,-1.],[.9,.001,1.7]])
def test_shared_side_distance_cancels_apex_quadratic_exactly_even_outside(apex):
    p=M.vector([1.,2.,3.]);q=M.vector([2.,4.,5.]);a=M.vector(apex)
    own=M.vector([3.,1.,4.]);nei=M.vector([-2.,5.,1.])
    sf=M.mul(M.cross(M.sub(q,p),M.sub(a,p)),F(1,2))
    fc=M.mul(M.add(M.add(p,q),a),F(1,3))
    co=M.mul(M.add(M.mul(own,3),a),F(1,4))
    cn=M.mul(M.add(M.mul(nei,3),a),F(1,4))
    assert M.value(M.side_distance(p,q,own,True),apex)==M.dot(sf,M.sub(fc,co))
    assert M.value(M.side_distance(p,q,nei,False),apex)==M.dot(sf,M.sub(cn,fc))

def test_volume_is_affine_and_original_polygon_moment_not_candidate_is_reference():
    sf,bc=M.planar_polygon([[0.,0.,0.],[0.,2.,0.],[3.,2.,0.],[3.,0.,0.]])
    assert bc==(F(3,2),F(1),F(0))
    volume=M.cone_volume(sf,bc)
    assert M.value(volume,[.1,.4,3.])==6
    assert M.value(volume,[20.,-7.,3.])==6

@pytest.mark.parametrize("do,dn,vo,vn",[(1.,1.,1.,1.),(.01,1.,1.,1.),(1.,1.,.005,1.),(.08,1.,.03,1.)])
def test_positive_branch_halfspaces_equal_installed_min_over_sum_and_min_over_max(do,dn,vo,vn):
    vals=[M.affine((0,0,0),x) for x in [do,dn,vo,vn]]
    rows=M.quality_rows(*vals)
    rational=[F.from_float(x) for x in [do,dn,vo,vn]]
    a,b,c,d=rational
    installed_w=min(a,b)/(a+b+M.VSMALL)
    installed_r=min(c,d)/(max(c,d)+M.VSMALL)
    passed=all(M.value(row,[0,0,0])>=0 for row in rows.values())
    assert passed==(installed_w>=M.WEIGHT and installed_r>=M.RATIO)

def test_fixed_external_face_distance_is_affine_with_neighbour_held_frozen():
    sf,fc,bc=M.vector([2,3,4]),M.vector([.2,.5,.7]),M.vector([1.,2.,3.])
    for a in [[.1,.2,.3],[3.,5.,7.]]:
        cc=M.mul(M.add(M.mul(bc,3),M.vector(a)),F(1,4))
        assert M.value(M.fixed_face_distance(sf,fc,bc,True),a)==M.dot(sf,M.sub(fc,cc))
        assert M.value(M.fixed_face_distance(sf,fc,bc,False),a)==M.dot(sf,M.sub(cc,fc))

def test_bad_abs_distance_branch_is_not_mislabeled_quality_feasible():
    rows=M.quality_rows(M.affine((0,0,0),-1),M.affine((0,0,0),-1),
                       M.affine((0,0,0),1),M.affine((0,0,0),1))
    assert M.value(rows["ownerPositivePyramid"],[0,0,0])<0
    # Stock abs distances can produce weight .5 outside this physical branch.
    assert abs(-1)/(abs(-1)+abs(-1))==.5

def test_exact_farkas_certificate_is_a_constant_contradiction_not_just_solver_status():
    rows=[M.affine([1,0,0],-1),M.affine([-1,0,0],0)]
    solved=M.solve_kernel(rows,[0,0,0],1)
    cert=solved["certificate"]
    normalized=M.normalize_rows(rows,[0,0,0],1)
    combination=[sum((F(lam)*normalized[i][j] for i,lam in zip(cert["indices"],cert["multipliers"])),F(0)) for j in range(4)]
    assert combination[:3]==[0,0,0]
    assert combination[3]<0
    assert str(combination[3])==cert["constantExact"]

def test_cube_quality_kernel_finds_an_interior_candidate():
    rows=[M.affine([1,0,0],0),M.affine([-1,0,0],1),
          M.affine([0,1,0],0),M.affine([0,-1,0],1),
          M.affine([0,0,1],0),M.affine([0,0,-1],1)]
    result=M.solve_kernel(rows,[.1,.2,.3],1)
    assert result["minimumNormalizedSlack"]>0
    assert all(M.value(row,result["candidate"])>0 for row in rows)

def test_exact_model_refuses_warp_and_nonsimple_boundary():
    with pytest.raises(ValueError,match="exact planar"):
        M.planar_polygon([[0,0,0],[1,0,0],[1,1,1e-16],[0,1,0]])
    with pytest.raises(ValueError):
        M.planar_polygon([[0,0,0],[1,1,0],[0,1,0],[1,0,0]])

@pytest.mark.parametrize("parent",[30503,25237,30263,54186])
def test_actual_rank1_exact_subset_excludes_single_apex_without_projecting_points(parent):
    document=json.loads((EVIDENCE/"latest-rank1-input.json").read_text())
    doc=next(v for v in document["stencils"] if v["parent"]==parent)
    rows,strict,names,faces,models,omitted,approx=M.model_stencil(doc,False)
    coords=np.array(list(doc["points"].values()))
    length=float(np.ptp(coords,axis=0).max())
    result=M.solve_kernel(rows,doc["originalApex"],length,strict)
    assert result["status"]==2 and result["infeasibleCertified"]
    cert=result["certificate"];normalized=M.normalize_rows(rows,doc["originalApex"],length)
    combination=[sum((F(lam)*normalized[i][j] for i,lam in zip(cert["indices"],cert["multipliers"])),F(0)) for j in range(4)]
    assert combination[:3]==[0,0,0] and combination[3]<0
    assert not approx

@pytest.mark.parametrize("parent",[9777,9792])
def test_actual_candidate_rechecks_every_child_quad_and_affected_face(parent):
    document=json.loads((EVIDENCE/"latest-rank3-input.json").read_text())
    doc=next(v for v in document["stencils"] if v["parent"]==parent)
    result=M.analyze(doc)
    q=result["candidateActual"]
    assert len(q["cells"])==len(doc["children"])
    assert len(q["faces"])==len(doc["affectedFaces"])
    assert q["minimumDeterminant"]>=.001 and q["minimumWeight"]>=.05 and q["minimumRatio"]>=.01
    assert q["maximumSkewness"]<=4 and q["minimumParentPlaneMarginM"]>3e-6
    assert q["minimumPyramidVolume"]>0 and q["minimumCentreFanTetQuality"]>0 and q["minimumSharedBasePointTetQuality"]>0
    assert q["allEdgesClosed"] and q["concavityPairTriggers"]==0
    assert q["relativeVolumeDifference"]<1e-10 and q["firstMomentDifference"]<1e-10
    assert all(v["noNewFailures"] and v["countDoesNotIncrease"] for v in result["candidateActualPareto"].values())

def test_actual_wr_only_proposal_that_creates_new_bad_det_or_nonorth_is_kept_as_rejected_scope():
    document=json.loads((EVIDENCE/"latest-rank3-input.json").read_text())
    doc=next(v for v in document["stencils"] if v["parent"]==621818)
    result=M.analyze(doc)
    assert result["candidateActual"]["minimumWeight"]>.05
    assert result["candidateActual"]["minimumRatio"]>.01
    assert result["candidateActualPareto"]["determinant"]["newlyBad"]
    assert result["candidateActualPareto"]["nonOrthogonality"]["newlyBad"]


def test_final_cross_parent_weight_rows_exactly_exclude_only_one_parent_apex_repair():
    document=json.loads((EVIDENCE/"final-ratio-parents-input.json").read_text())
    doc=next(v for v in document["stencils"] if v["parent"]==652847)
    rows,strict,names,faces,models,omitted,approx=M.model_stencil(doc,False)
    assert len(models)==10 and not omitted and not approx
    coords=np.array(list(doc["points"].values()))
    length=float(np.ptp(coords,axis=0).max())
    solved=M.solve_kernel(rows,doc["originalApex"],length,strict)
    cert=solved["certificate"]
    assert solved["infeasibleCertified"]
    assert [names[i] for i in cert["indices"]]==["37870:weightNeighbour","88011:weightNeighbour"]
    normalized=M.normalize_rows(rows,doc["originalApex"],length)
    combination=[sum((F(lam)*normalized[i][j] for i,lam in zip(cert["indices"],cert["multipliers"])),F(0)) for j in range(4)]
    assert combination[:3]==[0,0,0]
    assert str(combination[3])==cert["constantExact"] and combination[3]<0


def test_final_ratio_parent_wr_candidate_remains_refused_when_new_nonorthogonal_face_appears():
    document=json.loads((EVIDENCE/"final-ratio-parents-input.json").read_text())
    doc=next(v for v in document["stencils"] if v["parent"]==9836)
    result=M.analyze(doc)
    q=result["candidateActual"]
    assert q["minimumDeterminant"]>.001 and q["minimumWeight"]>.05 and q["minimumRatio"]>.01
    assert q["maximumSkewness"]<4
    veto=result["candidateActualPareto"]["nonOrthogonality"]
    assert veto["newlyBad"]==[1467321]
    assert not veto["countDoesNotIncrease"] and not veto["worstFailedSeverityDoesNotIncrease"]
