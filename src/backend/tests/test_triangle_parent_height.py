import importlib.util
from fractions import Fraction
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[3]/"tools/audit_air_triangle_parent_height.py"
S=importlib.util.spec_from_file_location("triangle_parent",P);M=importlib.util.module_from_spec(S);S.loader.exec_module(M)
TRI=[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]

@pytest.mark.parametrize("selected",[3,4])
def test_thin_parent_restricts_a_free_feasible_bound(selected):
    free=M.B.fixed_triangle_bound(TRI,selected)
    cap=M.triangle_parent_bound(TRI,selected,Fraction(1,10**8))
    assert not free["necessaryFails001"]
    assert cap["maximumHeightBelowUnconstrainedOptimum"]
    assert cap["parentBoundNecessaryFails001"]
    assert Fraction(cap["exactUpperBoundFraction"]) < Fraction(1,1000)

@pytest.mark.parametrize("selected",[3,4])
def test_free_height_limit_matches_general_triangle_envelope(selected):
    cap=M.triangle_parent_bound(TRI,selected,Fraction(100))
    free=M.B.fixed_triangle_bound(TRI,selected)
    assert not cap["maximumHeightBelowUnconstrainedOptimum"]
    assert float(Fraction(cap["exactUpperBoundFraction"])) == pytest.approx(free["bound"],rel=2e-15)
    assert cap["necessaryOnlyNotFeasibility"]

def test_unsupported_masks_and_nontriangle_refuse():
    with pytest.raises(ValueError):M.triangle_parent_bound(TRI,2,Fraction(1))
    with pytest.raises(ValueError):M.triangle_parent_bound(TRI+[TRI[0]],4,Fraction(1))
    with pytest.raises(ValueError):M.triangle_parent_bound(TRI,4,Fraction(-1))
