"""Necessary convex cuts and exact certificates, never native acceptance."""
from fractions import Fraction as F
from functools import lru_cache
import json
import math
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_tetra_convex_apex_kernel as M
import audit_air_cone_quality_kernel as K
import audit_air_cone_candidate_independent as I
D=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-whole-parent-apex-optimization"
STEMS=["rank0-parent19921","rank3-parent10906","rank6-parent9316"]

@lru_cache()
def doc(stem):return json.loads((D/(stem+"-input.json")).read_text())
@lru_cache()
def report(stem):return json.loads((D/"convex-kernel"/(stem+".json")).read_text())
@lru_cache()
def actual(stem):return K.actual_stencil_quality(doc(stem),doc(stem)["originalApex"])
@lru_cache()
def entries(stem):return M.qualified_tets(doc(stem),actual(stem))
@lru_cache()
def models(stem):return K.model_stencil(doc(stem),approximate=False)

def synthetic_entry(base,apex,selected):
    p=[K.vector(x) for x in base]
    b=K.cross(K.sub(p[1],p[0]),K.sub(p[2],p[0]))
    h=K.affine(b,-K.dot(b,p[0]))
    if K.value(h,apex)<0:h=K.scale(h,-1)
    return {"cell":0,"baseFace":0,"selectedCount":selected,"areaSquared":K.dot(b,b)/4,
      "height":h,"sides":[M.area_affine([p[i],p[(i+1)%3],apex],2) for i in range(3)]}

@pytest.mark.parametrize("selected",[3,4])
def test_correct_area_power_formula_is_scale_invariant_and_covers_outside_projection(selected):
    base=np.array([[0.,0.,0.],[2.,0.,0.],[0.,3.,0.]])
    generator=np.random.default_rng(357139)
    for scale in [.1,.5,1.,3.]:
        for apex in generator.uniform([-3,-3,.1],[6,6,5],size=(30,3)):
            entry=synthetic_entry(scale*base,scale*apex,selected)
            sides=np.array([I.face_geometry(np.array([scale*base[i],scale*base[(i+1)%3],scale*apex]))[0] for i in range(3)])
            sf=sides if selected==3 else np.vstack([sides,-I.face_geometry(scale*base)[0]])
            avg=np.linalg.norm(sf,axis=1).mean()
            actual_d=abs(np.linalg.det((sf/avg).T@(sf/avg)))/8
            assert M.determinant_formula(entry,scale*apex)==pytest.approx(actual_d,rel=3e-12,abs=1e-16)

def test_original_hypothesis_failure_is_preserved_as_a_scaled_counterexample():
    raw=json.loads((D/"convex-kernel/proposed-formula-counterexample.json").read_text())
    assert len(raw["rows"])==8
    for selected in [3,4]:
        rows=[r for r in raw["rows"] if r["selectedFaces"]==selected]
        actuals=[r["actualAreaTensorD"] for r in rows]
        np.testing.assert_allclose(actuals,actuals[0],rtol=2e-14)
        for row in rows:
            assert row["actualAreaTensorD"]==pytest.approx(row["correctFormulaD"],rel=2e-14)
        assert abs(rows[1]["actualAreaTensorD"]/rows[1]["proposedIncorrectFormulaD"]-36)<1e-12
        assert rows[0]["proposedIncorrectFormulaD"]!=rows[-1]["proposedIncorrectFormulaD"]

@pytest.mark.parametrize("stem",STEMS)
def test_all_actual_qualified_bad_tets_match_the_original_selected_sf_formula(stem):
    bycell={c["cell"]:c for c in actual(stem)["cells"]}
    assert len(entries(stem))==report(stem)["qualifiedActualBadTets"]
    for entry in entries(stem):
        assert M.determinant_formula(entry,doc(stem)["originalApex"])==pytest.approx(bycell[entry["cell"]]["D"],rel=3e-12,abs=1e-16)

@pytest.mark.parametrize("selected",[3,4])
def test_convex_function_jensen_and_determinant_threshold_equivalence(selected):
    generator=np.random.default_rng(817401)
    base=np.array([[0.,0.,0.],[2.,0.,0.],[0.,3.,0.]])
    entry=synthetic_entry(base,[.5,.5,1.],selected)
    for _ in range(300):
        p,q=generator.uniform([-4,-4,.05],[5,5,6],size=(2,3));t=generator.uniform(0,1)
        gp,gq=M.convex_value(entry,p),M.convex_value(entry,q)
        assert M.convex_value(entry,t*p+(1-t)*q)<=t*gp+(1-t)*gq+2e-13
        assert (gp<=0)==(M.determinant_formula(entry,p)>=.001)

@pytest.mark.parametrize("selected",[3,4])
def test_rational_support_cut_is_a_global_necessary_outer_halfspace(selected):
    generator=np.random.default_rng(192617)
    base=np.array([[0.,0.,0.],[2.,0.,0.],[0.,3.,0.]])
    entry=synthetic_entry(base,[.5,.5,1.],selected)
    row,meta=M.determinant_cut(entry,[.2,.3,.8])
    assert F(meta["aUpper"])**6>=F(meta["aSixthPowerExact"])
    assert F(meta["bUpper"])**6>=F(meta["bSixthPowerExact"])
    assert F(meta["baseAreaLower"])**2<=F(meta["areaSquaredExact"])
    for unit in meta["unitNormSupportsExact"]:assert sum(F(x)**2 for x in unit)<=1
    accepted=0
    for point in generator.uniform([-10,-10,.005],[10,10,10],size=(500,3)):
        if M.determinant_formula(entry,point)>=.001:
            accepted+=1
            assert K.value(row,point)>=0
    assert accepted>100

def test_directed_root_bounds_are_exact_and_never_reverse_necessity():
    for value in [F(0),F(1),F(2),F(1,1000000000000),F(379,19),F(1<<200,173)]:
        for degree in [2,6]:
            lo,hi=M.root_bounds(value,degree)
            assert lo**degree<=value<=hi**degree
            assert hi-lo<=F(1,1<<96)
    with pytest.raises(ValueError):M.root_bounds(-1,6)

def test_machin_identity_and_alternating_cosine_lower_bound_direction():
    q=F(1,5);tan4=4*q*(1-q*q)/(1-6*q*q+q**4);b=F(1,239)
    assert (tan4-b)/(1+tan4*b)==1
    lo5,hi5=M.atan_bounds(q);lo239,hi239=M.atan_bounds(b)
    assert 3<16*lo5-4*hi239<=16*hi5-4*lo239<F(22,7)
    cosine=M.cosine70_lower()
    # Exact alternating-series direction is implemented with outward bounds;
    # floating comparison here only confirms the value, not certificate validity.
    assert float(cosine)==pytest.approx(math.cos(math.radians(70)),abs=2e-16)

@pytest.mark.parametrize("stem",STEMS)
def test_every_certificate_rebuilds_from_original_necessary_cuts_and_exact_fractions(stem):
    raw=report(stem);cert=raw["exactSeparationCertificate"]
    assert cert is not None and raw["allLocalFiveMetricPassingCandidateCount"]==0
    base_rows,_,names,face_models,child_models,omitted,approx=models(stem)
    assert not omitted and not approx
    byname=dict(zip(names,base_rows));bycell={e["cell"]:e for e in entries(stem)}
    rebuilt=[]
    for support in cert["supportRows"]:
        meta=support["derivation"]
        if meta["kind"]=="exactOriginalKernelOrWR":
            row=byname[meta["name"]]
        elif meta["kind"]=="tetDeterminantNecessary":
            row,details=M.determinant_cut(bycell[meta["cell"]],meta["query"])
            assert F(details["aUpper"])**6>=F(details["aSixthPowerExact"])
            assert F(details["bUpper"])**6>=F(details["bSixthPowerExact"])
        elif meta["kind"]=="nonorthNecessary":
            found=M.nonorth_cuts(doc(stem),face_models,child_models,meta["query"])
            row,details=next((r,m) for r,m in found if m["face"]==meta["face"])
            assert sum(F(x)**2 for x in details["unitNormSupportExact"])<=1
        else:raise AssertionError(meta["kind"])
        normalized=K.normalize_rows([row],cert["normalizedOrigin"],cert["normalizedScale"])[0]
        assert normalized==tuple(F(x) for x in support["rowExact"])
        rebuilt.append(normalized)
    weights=[F(x) for x in cert["multipliers"]]
    assert all(w>=0 for w in weights) and sum(weights)==1
    combined=[sum(w*r[j] for w,r in zip(weights,rebuilt)) for j in range(4)]
    assert combined[:3]==[0,0,0]
    assert combined[3]<0 and str(combined[3])==cert["constantExact"]
    assert [str(x) for x in combined[:3]]==cert["gradientExact"]

@pytest.mark.parametrize("stem",["rank0-parent20937","rank4-parent12113","rank6-parent14771"])
def test_unknown_coupled_exterior_state_is_still_refused(stem):
    with pytest.raises(ValueError,match="coupled outside state"):
        M.analyze(doc(stem),maximum_rounds=1)
