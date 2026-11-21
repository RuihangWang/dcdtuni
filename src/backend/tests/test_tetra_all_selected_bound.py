"""Algebra and installed determinant masks, without a native job."""
from pathlib import Path
import importlib.util
from fractions import Fraction
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
SPEC=importlib.util.spec_from_file_location("alltet",ROOT/"tools/audit_air_tetra_all_selected_bound.py")
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

def outward_tetra_areas(base,apex):
    sf=.5*np.cross(base[1]-base[0],base[2]-base[0])
    if np.dot(sf,apex-base[0])>0:
        base=base[[0,2,1]];sf=-sf
    sides=[.5*np.cross(base[i]-base[(i+1)%3],apex-base[(i+1)%3]) for i in range(3)]
    return sf,np.asarray(sides)

@pytest.mark.parametrize("apex",[[.2,.3,.4],[3.,-7.,.4],[-2.,9.,-1.],[-.8,.6,3.]])
def test_four_face_cauchy_binet_and_height_formula_with_projection_outside(apex):
    base=np.array([[0.,0.,0.],[1.,0.,0.],[.3,.2,0.]])
    apex=np.asarray(apex);sf,sides=outward_tetra_areas(base,apex)
    areas=np.vstack((sf,sides))
    assert np.linalg.norm(areas.sum(axis=0))<1e-13
    A=np.linalg.norm(sf);h=abs(apex[2]);S=np.linalg.norm(sides,axis=1).sum()
    raw_gram=sum(np.outer(x,x) for x in areas)
    assert np.linalg.det(raw_gram)==pytest.approx(4*np.linalg.det(sides)**2,rel=1e-12,abs=1e-18)
    assert np.linalg.det(raw_gram)==pytest.approx(A**4*h**4,rel=1e-12,abs=1e-18)
    actual=M.M.q.determinant(areas)[0]
    assert actual==pytest.approx(512*A**4*h**4/(A+S)**6,rel=1e-12,abs=1e-13)
    assert actual<=M.fixed_triangle_bound(base,4)["bound"]+1e-13

def test_equilateral_regular_tetrahedron_attains_sharp_four_face_bound():
    base=np.array([[0.,0.,0.],[1.,0.,0.],[.5,np.sqrt(3.)/2,0.]])
    apex=np.array([.5,np.sqrt(3.)/6,np.sqrt(2./3)])
    sf,sides=outward_tetra_areas(base,apex)
    actual=M.M.q.determinant(np.vstack((sf,sides)))[0]
    assert actual==pytest.approx(8/27,abs=1e-13)
    assert actual==pytest.approx(M.fixed_triangle_bound(base,4)["bound"],abs=1e-13)

def test_exact_same_triangle_branch_constants_must_not_apply_physical_bound_to_internal_tet():
    base=[[0.,0.,0.],[1.,0.,0.],[.5,.02,0.]]
    physical=M.fixed_triangle_bound(base,3);internal=M.fixed_triangle_bound(base,4)
    assert Fraction(internal["exactUpperBoundFraction"])/Fraction(physical["exactUpperBoundFraction"])==Fraction(128,54)
    assert internal["selectedFaceCount"]==4 and internal["boundConstant"]==128
    assert physical["selectedFaceCount"]==3 and physical["boundConstant"]==54
    with pytest.raises(ValueError):M.fixed_triangle_bound(base,2)

def test_actual_ear_internal_tet_certificates_and_source_outline_refan_keep_scope():
    import json
    directory=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-ear-internal-tet-bound-independent"
    audit=json.loads((directory/"actual-ear-selected-mask-bound-audit.json").read_text())
    assert audit["counts"]["actualDCells"]==1321
    assert audit["counts"]["selected4Tetrahedra"]==550
    assert audit["counts"]["selected3Tetrahedra"]==522
    assert audit["counts"]["selected4CertifiedImpossible"]==66
    assert audit["counts"].get("selected3CertifiedImpossible",0)==0
    assert all(row["selectedFaceCount"]==4 and row["sourceBaseArity"]==5
               and row["sourceBasePatch"]==row["sourceBaseZone"]==-1
               and Fraction(row["exactUpperBoundFraction"])<Fraction.from_float(.001)
               for row in audit["certifiedExclusions"])
    proof=json.loads((directory/"strong128-source-n5-refan-candidates.json").read_text())
    assert proof["originalSourceFaces"]==proof["withNecessaryEligibleRefan"]==33
    for row in proof["bases"]:
        assert len(row["strong128EligibleCandidates"])==4
        selected=row["selected"]["partition"]
        assert sorted(map(len,selected["bases"]))==[3,4]
        assert set(sum(selected["bases"],[]))==set(row["sourcePointLabels"])
        assert selected["areaError"]<1e-12
        assert selected["signedMomentError"]<1e-12
        assert row["noActualParentApexProfileOrWholeParentAcceptance"]

def test_written_apex_requires_all_children_unique_new_point_not_source_boundary_node():
    assert M.shared_apex_from_children([[1,2,7],[2,3,7],[3,4,7]],np.array([0,1,2,3,4,5,6,11]),10)==7
    with pytest.raises(ValueError):M.shared_apex_from_children([[1,2,7],[1,3,7]],np.arange(8)+10,10)
    with pytest.raises(ValueError):M.shared_apex_from_children([[1,2,7],[2,3,7],[3,4,7]],np.arange(8),10)

def test_actual_ear_written_apex_has_new_topology_incidence_and_honest_determinant_failures():
    import json
    directory=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-ear-internal-tet-bound-independent"
    doc=json.loads((directory/"actual-ear-written-apex-and-determinants.json").read_text())
    assert doc["writtenParentCount"]==6
    assert doc["noOldCellLabelReuse"]
    for row in doc["parents"]:
        assert row["writtenApexExactlyMatchesInputWitness"]
        assert len(row["allWrittenChildren"])>=16
        if row["sourceBase"] in [1958933,1958934]:
            assert row["minimumActualWrittenBaseChildDeterminant"]<.001
        else:
            assert row["minimumActualWrittenBaseChildDeterminant"]>.02
