"""Projection-independent exact cone numerator and selected-mask bounds."""
from pathlib import Path
import importlib.util
from fractions import Fraction as F
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
SPEC=importlib.util.spec_from_file_location("pyramid",ROOT/"tools/audit_air_pyramid_remaining_bound.py")
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

@pytest.mark.parametrize("base",[
 [[0.,0.,0.],[1.,0.,0.],[.3,.2,0.]],
 [[0.,0.,0.],[2.,0.,0.],[1.5,1.,0.],[0.,1.,0.]],
 [[0.,0.,0.],[1.,0.,1.],[1.5,1.,2.5],[0.,1.,1.]]])
@pytest.mark.parametrize("physical",[True,False])
def test_exact_gram_height4_independence_for_arbitrary_projection_and_bound(base,physical):
    p,sf,area2,sideK,allK=M.cone_shape(base)
    for scale in [F(1,5),F(1),F(3)]:
        for shift in [F(-9),F(0),F(11)]:
            apex=M.add(M.add(p[0],M.mul(sf,scale)),M.mul(M.sub(p[1],p[0]),shift))
            record=M.raw_formula(base,apex,physical)
            assert record["projectionIndependentHeight4IdentityExact"]
            assert record["predictedDeterminant"]<=M.fixed_polygon_cone_bound(base,physical)["bound"]+1e-12

def test_triangle_general_formula_reduces_exactly_to_54_and_128_branches():
    base=[[0.,0.,0.],[1.,0.,0.],[.5,.02,0.]]
    for physical,selected in [(True,3),(False,4)]:
        generic=M.fixed_polygon_cone_bound(base,physical)
        triangle=M.B.fixed_triangle_bound(base,selected)
        assert F(generic["exactUpperBoundFraction"])==F(triangle["exactUpperBoundFraction"])

@pytest.mark.parametrize("physical,height,expected",[(True,np.sqrt(.5),8/27),(False,np.sqrt(2.),.59604644775390625)])
def test_square_pyramid_attains_general_bound_at_incenter_height(physical,height,expected):
    base=[[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]]
    row=M.raw_formula(base,[.5,.5,height],physical)
    assert row["predictedDeterminant"]==pytest.approx(expected,abs=1e-13)
    assert row["predictedDeterminant"]==pytest.approx(M.fixed_polygon_cone_bound(base,physical)["bound"],abs=1e-13)

def test_exact_bound_refuses_warped_or_concave_base_instead_of_rounding_it_into_certificate():
    with pytest.raises(ValueError):M.fixed_polygon_cone_bound([[0.,0.,0.],[1.,0.,0.],[1.,1.,1e-14],[0.,1.,0.]],False)
    with pytest.raises(ValueError):M.fixed_polygon_cone_bound([[0.,0.,0.],[1.,0.,0.],[.1,.1,0.],[0.,1.,0.]],False)

@pytest.mark.parametrize("physical",[True,False])
def test_parent_height_bound_dominates_actual_cones_even_with_outside_projection(physical):
    base=[[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]]
    for hmax in [.001,.1,.5,1.,2.]:
        bound=M.bounded_height_formula(base,physical,F.from_float(hmax)**2)
        for fraction in [.05,.5,1.]:
            for xy in [[.5,.5],[-3.,7.],[1.,0.]]:
                d=M.raw_formula(base,[*xy,hmax*fraction],physical)["predictedDeterminant"]
                assert d<=bound["bound"]+1e-13
        assert bound["bound"]<=M.fixed_polygon_cone_bound(base,physical)["bound"]+1e-13

@pytest.mark.parametrize("physical,expected_factor",[(True,8),(False,32)])
def test_correct_free_height_branch_is_selected(physical,expected_factor):
    base=[[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]]
    record=M.bounded_height_formula(base,physical,F(100))
    assert F(record["unconstrainedOptimalHeightSquaredUsingLowerPerimeterExactFraction"])==F(expected_factor,16)
    assert not record["maximumHeightBelowUnconstrainedOptimum"]

def box_trace(height=.001,open_shell=False):
    points=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],
                     [0,0,height],[1,0,height],[1,1,height],[0,1,height]],float)
    faces=[[3,2,1,0],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
    if open_shell:faces=faces[:-1]
    return {"points":points,"vertices":np.array(sum(faces,[])),
            "offsets":np.r_[0,np.cumsum([len(f) for f in faces])],
            "owner":np.zeros(len(faces),int),"sourceCellFaces":np.arange(len(faces)),
            "sourceCellOffsets":np.array([0,len(faces)])}

def test_height_cap_uses_only_original_source_vertices_and_exact_original_plane():
    trace=box_trace()
    base=trace["points"][:4]
    row=M.source_parent_height_bound(trace,0,base,[.5,.5,.0005],False)
    assert row["sourceOnlyHeightCapCertified"]
    assert row["directOriginalHeightCapFaceLabels"]==[1]
    assert row["maximumSourceVertexInwardHeightM"]==pytest.approx(.001)
    assert row["sourceVertexLabels"]==list(range(8))
    assert row["parentKernelNecessaryBound"]["parentBoundNecessaryFails001"]
    assert row["parentKernelNecessaryBound"]["bound"]<.001

def test_open_source_shell_cannot_become_a_parent_height_certificate():
    trace=box_trace(open_shell=True)
    row=M.source_parent_height_bound(trace,0,trace["points"][:4],[.5,.5,.0005],False)
    assert not row["sourceDirectedEdgesClosedExactly"]
    assert not row["sourceOnlyHeightCapCertified"]
    assert "parentKernelNecessaryBound" not in row
