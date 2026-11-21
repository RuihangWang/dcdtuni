"""Pure math branch and provenance classification, without a native case."""
import importlib.util
from fractions import Fraction
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
SPEC=importlib.util.spec_from_file_location("refan_ancestry",ROOT/"tools/audit_air_refan_remaining_ancestry.py")
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

def test_strong_bound_is_sharper_and_perimeter_lower_is_rigorous():
    row=M.triangle_strong_bound([[0.,0.,0.],[1.,0.,0.],[.5,.02,0.]])
    bound=Fraction(row["exactUpperBoundFraction"]);old=Fraction(row["oldExactUpperBoundFraction"])
    assert bound < old/4
    for lo,sq in zip(row["edgeLengthLowerFractions"],row["edgeLengthSquaredExactFractions"]):
        assert Fraction(lo)**2 <= Fraction(sq)
    assert row["strongNecessaryFails001"]
    assert not row["oldNecessaryFails001"]

@pytest.mark.parametrize("apex",[[.3,.2,.4],[2.,3.,.4],[-4.,-2.,-1.],[.2,.5,3.]])
def test_installed_selected_three_side_det_never_exceeds_strong_bound_outside_projection(apex):
    base=np.array([[0.,0.,0.],[1.,0.,0.],[.3,.2,0.]])
    apex=np.array(apex);areas=[]
    for i in range(3):
        a,b=base[i],base[(i+1)%3]
        areas.append(.5*np.cross(b-a,apex-a))
    actual=M.q.determinant(areas)[0]
    bound=M.triangle_strong_bound(base)["bound"]
    assert actual <= bound+1e-13
    # The Minkowski premise keeps signed normal projections outside the base.
    A=np.linalg.norm(.5*np.cross(base[1]-base[0],base[2]-base[0]))
    P=sum(np.linalg.norm(base[(i+1)%3]-base[i]) for i in range(3))
    h=abs(apex[2]);S=sum(np.linalg.norm(a) for a in areas)
    assert S**2 >= A**2+(h*P/2)**2-1e-13

def test_equilateral_attains_derived_shape_bound_at_optimal_height():
    base=np.array([[0.,0.,0.],[1.,0.,0.],[.5,np.sqrt(3.)/2,0.]])
    A=np.sqrt(3.)/4;P=3.;h=np.sqrt(8.)*A/P
    apex=np.array([.5,np.sqrt(3.)/6,h])
    areas=[.5*np.cross(base[(i+1)%3]-base[i],apex-base[i]) for i in range(3)]
    assert M.q.determinant(areas)[0] == pytest.approx(54*A*A/P**4,abs=1e-13)

def test_same_base_relation_requires_same_parent_and_resolved_base():
    assert M.face_relation(7,7,11,11)=="sameParentSameSourceBaseAPInvariant"
    assert M.face_relation(7,8,11,11)=="crossParentInterface"
    assert M.face_relation(7,7,11,12)=="sameParentDifferentSourceBase"
    assert M.face_relation(7,7,-1,12)=="sameParentBaseIdentityAmbiguous"
    assert M.face_relation(7,None,11,-1)=="boundaryOrCoupledNotLocallyPaired"

@pytest.mark.parametrize("points",[[[0.,0.,0.],[1.,0.,0.],[2.,0.,0.]],
                                  [[0.,0.,0.],[1.,0.,0.]]])
def test_bound_refuses_degenerate_or_nontriangular_input(points):
    with pytest.raises(ValueError):M.triangle_strong_bound(points)

def test_actual_source_axis_kernel_is_json_safe_and_detects_already_centre():
    points=np.array([[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.],
                     [0.,0.,1.],[1.,0.,1.],[1.,1.,1.],[0.,1.,1.]])
    loops=[[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
    trace={"faceAlive":np.ones(6),"owner":np.zeros(6,dtype=int),
           "neighbour":np.full(6,-1),"offsets":np.arange(0,25,4),
           "vertices":np.array(sum(loops,[])),"points":points,
           "apex":np.array([[.5,.5,.5]])}
    row=M.source_parent_kernel(trace,0)
    assert row["allSourceFacesAxisAligned"] is True
    assert row["sourceApexEqualsNativeAxisCentre"] is True
    assert row["boxCentreMinimumAllOriginalPlaneMarginM"] == pytest.approx(.5)
    import json
    json.dumps(row)

def test_generic_ear_union_preserves_simple_concave_outline_signed_moment_and_strict_pieces():
    points={i:p for i,p in enumerate([[0.,0.,0.],[2.,0.,0.],[2.,1.,0.],[1.,1.,0.],[1.,2.,0.],[0.,2.,0.]])}
    result=M.general_refan_polygon(list(points),points)
    assert result["selected"] is not None
    assert result["validTriangulations"]>0
    assert result["reference"]["area"] == pytest.approx(3.)
    assert result["reference"]["signedAreaCentre"] == pytest.approx([5/6,5/6,0.])
    selected=result["selected"]
    assert set(sum(selected["bases"],[]))==set(points)
    assert selected["areaError"]<1e-12
    assert selected["signedMomentError"]<1e-12
    assert selected["minimumWithinBaseWeight"]>=.05
    assert selected["minimumWithinBaseRatio"]>=.01

def test_actual_two_notched_bases_need_other_diagonal_and_keep_old_ap_failure_honest():
    import json
    raw=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-joint-remaining-parent-independent/strong-trigger-canonical-refan-candidates-v2.json"
    doc=json.loads(raw.read_text())
    notched=[row for row in doc["bases"] if len(row["sourcePointLabels"])==8]
    assert len(notched)==2
    for row in notched:
        assert row["refan"]["necessaryEligibleCount"]==0
        assert row["generalTriangulation"]["validTriangulations"]==19
        assert row["generalTriangulation"]["necessaryEligibleCount"]==5
        assert len(row["generalTriangulation"]["selected"]["bases"])==4
        assert row["localActualSourceApexProfile"]["minimumLocalDeterminant"]<.001
        assert row["localActualSourceApexProfile"]["allLocalConeHalfspacesAndClosure"]
        assert row["localActualSourceApexProfile"]["omitsOtherOriginalBaseInterfacesAndWholeParent"]

def test_general_triangulation_selection_uses_coordinates_across_cycle_reversal_and_relabel():
    import json
    raw=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-joint-remaining-parent-independent/strong-trigger-canonical-refan-candidates-v2.json"
    row=next(row for row in json.loads(raw.read_text())["bases"] if len(row["sourcePointLabels"])==8)
    ids=row["sourcePointLabels"];points=dict(zip(ids,row["sourceCoordinates"]))
    original=M.general_refan_polygon(ids,points)["selected"]
    remap={old:17000+43*i for i,old in enumerate(ids[::-1])}
    shifted_points={remap[old]:points[old] for old in ids}
    changed=M.general_refan_polygon([remap[x] for x in ids[::-1]],shifted_points)["selected"]
    def signature(result,coords):
        return sorted(tuple(tuple(coords[i]) for i in base) for base in result["bases"])
    assert signature(original,points)==signature(changed,shifted_points)

def test_general_triangulation_refuses_crossing_original_edges():
    with pytest.raises(ValueError):
        M.general_refan_polygon([0,1,2,3],{0:[0.,0.,0.],1:[2.,2.,0.],2:[0.,2.,0.],3:[2.,0.,0.]})

def test_actual_internal_collinear_edge_nodes_have_one_good_common_three_piece_partition():
    import json
    raw=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-joint-remaining-parent-independent/same-base-invariant-general-refan.json"
    rows=[row for row in json.loads(raw.read_text())["bases"] if len(row["originalSourceParents"])==2]
    assert {(row["rank"],row["sourceBase"]) for row in rows}=={(1,206811),(4,89115)}
    for row in rows:
        enum=row["generalTriangulation"]
        assert enum["validTriangulations"]==4
        assert enum["strictGeometryCandidates"]==7
        assert enum["necessaryEligibleCount"]==1
        selected=enum["selected"]
        assert sorted(map(len,selected["bases"]))==[3,3,4]
        assert set(sum(selected["bases"],[]))==set(row["sourcePointLabels"])
        assert selected["minimumWithinBaseWeight"]>.32
        assert selected["minimumWithinBaseRatio"]>.46
        assert not selected["strongPhysicalTriBounds"]
        for profile in row["sourceApexLocalProfiles"]:
            assert not profile["physicalBaseExcludedFromDeterminant"]
            assert profile["minimumLocalDeterminant"]>.02
            assert profile["allLocalConeHalfspacesAndClosure"]
            conservation=profile["originalBaseConeConservation"]
            assert conservation["relativeVolumeError"]<1e-10
            assert conservation["relativeFirstMomentError"]<1e-10
        across=row["acrossOriginalInternalBaseProfile"]
        assert across["minimumWeight"]>.3
        assert across["minimumVolumeRatio"]>.49
        assert across["maximumSkewness"]<.5
        assert across["maximumNonOrthogonality"]<26.
        assert across["noWholeParentOrOtherSourceBaseAcceptance"]

def test_local_cone_installed_determinant_mask_distinguishes_internal_from_physical_base():
    points={0:[0.,0.,0.],1:[1.,0.,0.],2:[0.,1.,0.]}
    internal=M.local_cone_profile([[0,1,2]],points,[.2,.3,.4],physical_base=False)
    physical=M.local_cone_profile([[0,1,2]],points,[.2,.3,.4],physical_base=True)
    assert internal["minimumLocalDeterminant"]!=pytest.approx(physical["minimumLocalDeterminant"])
    assert not internal["physicalBaseExcludedFromDeterminant"]
    assert physical["physicalBaseExcludedFromDeterminant"]
    assert internal["cells"][0]["volume"]==pytest.approx(physical["cells"][0]["volume"])
    for profile in [internal,physical]:
        assert profile["originalBaseConeConservation"]["relativeVolumeError"]<1e-12
        assert profile["originalBaseConeConservation"]["relativeFirstMomentError"]<1e-12


def test_full_connected_unions_and_all_cycle_equivalence_preserve_actual_selected_pieces():
    import json
    directory=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-joint-remaining-parent-independent"
    enum=json.loads((directory/"complete-ear-partitions-and-cycles.json").read_text())
    cycles=json.loads((directory/"complete-cycle-equivalence.json").read_text())
    assert len(enum["bases"])==len(cycles["bases"])==4
    assert sum(len(row["checkedCycles"]) for row in cycles["bases"])==56
    for row,equiv in zip(enum["bases"],cycles["bases"]):
        complete=row["fullConnectedGroupEnumeration"]
        limited=row["generalTriangulation"]
        assert complete["maximumTrianglesPerPiece"]==len(row["sourcePointLabels"])-2
        assert complete["necessaryEligibleCount"]==limited["necessaryEligibleCount"]
        assert complete["selected"]["bases"]==limited["selected"]["bases"]
        assert all(item["coordinateCycleAndPiecesExactlyMatch"] for item in equiv["checkedCycles"])


def test_actual_native_partition_matches_math_without_mislabeling_input_ap_as_final_written_ap():
    import json
    raw=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-joint-remaining-parent-independent/ear-native-four-base-partition-correspondence.json"
    doc=json.loads(raw.read_text())
    assert doc["inputStageApexIsNotFinalWrittenApex"]
    assert len(doc["bases"])==4
    for row in doc["bases"]:
        assert row["nativeSelectedMatchesActualPureMathCoordinateCycles"]
        assert row["olderJointInputCoordinateBitsExactlyMatch"]
        assert row["allNativePiecesUseExactlyOriginalWitnessPoints"]
        assert row["nativeWitness"]["qualityRefanSelected"]
        assert not row["nativeWitness"]["conservationFailure"]
        assert "nativeWitnessInputStageApexLocalProfiles" in row
        assert "actualNativeApexLocalProfiles" not in row
