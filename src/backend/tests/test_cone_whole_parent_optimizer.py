"""Frozen real-stencil tests for candidate construction and honest acceptance."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_cone_whole_parent_optimizer as O
import audit_air_cone_quality_kernel as K
import audit_air_cone_nonorth_kernel as N
D=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-whole-parent-apex-optimization"
SUPPORTED=["rank0-parent19921","rank3-parent10906","rank6-parent9316"]
REFUSED=["rank0-parent20937","rank4-parent12113","rank6-parent14771"]

@lru_cache()
def doc(stem):
    return json.loads((D/(stem+"-input.json")).read_text())

@lru_cache()
def report(stem):
    return json.loads((D/(stem+"-result.json")).read_text())

def compare_fast_to_full(stem,point):
    fixture=doc(stem);fast=O.FastStencil(fixture).evaluate(point)
    full=K.actual_stencil_quality(fixture,point)
    cells={row["cell"]:row for row in full["cells"]}
    faces={row["face"]:row for row in full["faces"]}
    np.testing.assert_allclose(fast["D"],[cells[c]["D"] for c in fixture["children"]],rtol=2e-12,atol=1e-15)
    np.testing.assert_allclose(fast["CC"],[cells[c]["CC"] for c in fixture["children"]],rtol=0,atol=2e-15)
    np.testing.assert_allclose(fast["V"],[cells[c]["V"] for c in fixture["children"]],rtol=2e-12,atol=1e-18)
    for metric,key in [("weight","weight"),("ratio","ratio"),("nonorth","nonOrthogonalityDegrees")]:
        np.testing.assert_allclose(fast[metric],[faces[f][key] for f in fast["internalFaceIDs"]],rtol=2e-12,atol=2e-12)
    np.testing.assert_allclose(fast["skew"],[faces[f]["skew"] for f in fast["faceIDs"]],rtol=2e-12,atol=2e-12)

@pytest.mark.parametrize("stem",SUPPORTED)
def test_fast_actual_sf_mask_and_every_interface_match_the_full_written_stencil(stem):
    compare_fast_to_full(stem,doc(stem)["originalApex"])
    # A geometry-derived interior perturbation tests moving-face effects, not
    # just a baseline that could conceal an incorrectly fixed face.
    _,_,seeds,_=O.seed_candidates(doc(stem))
    seed=next(point for name,point in seeds if name=="originalPlaneKernelBoxCentre")
    point=.8*np.asarray(doc(stem)["originalApex"])+.2*seed
    compare_fast_to_full(stem,point)

@pytest.mark.parametrize("stem",REFUSED)
def test_real_coupled_stencils_are_refused_without_invented_remote_geometry(stem):
    with pytest.raises(ValueError,match="coupled outside state"):
        O.FastStencil(doc(stem))

def test_pareto_constraint_floors_preserve_original_passing_roles_and_failed_worst_severity():
    original={"D":np.array([.002,.0004,.0008]),"weight":np.array([.2,.03,.04]),
              "ratio":np.array([.2,.005,.008]),"skew":np.array([1.,5.,6.]),
              "nonorth":np.array([50.,75.,80.])}
    floors=O.floors(original)
    np.testing.assert_array_equal(floors["D"],[.001,.0004,.0004])
    np.testing.assert_array_equal(floors["weight"],[.05,.03,.03])
    np.testing.assert_array_equal(floors["ratio"],[.01,.005,.005])
    np.testing.assert_array_equal(floors["skew"],[4.,6.,6.])
    np.testing.assert_array_equal(floors["nonorth"],[70.,80.,80.])
    assert O.LIMITS=={"D":.001,"weight":.05,"ratio":.01,"skew":4.,"nonorth":70.}

@pytest.mark.parametrize("stem",SUPPORTED[:2])
def test_saved_partial_candidate_passes_full_geometry_pareto_but_is_not_all_pass(stem):
    raw=report(stem);best=raw["bestGeometryAndParetoCandidate"]
    origin=K.actual_stencil_quality(doc(stem),doc(stem)["originalApex"])
    checked=N.evaluate(doc(stem),origin,best["apex"])
    assert checked["passesLocalGeometry"] and checked["passesLocalFiveMetricPareto"]
    assert checked["actual"]["minimumDeterminant"]>origin["minimumDeterminant"]*(1+raw["minimumRelativeDeterminantImprovement"])
    assert checked["actual"]["minimumDeterminant"]<.001
    assert checked["actual"]["maximumNonOrthogonalityDegrees"]>70
    assert not checked["allLocalFiveMetricsPass"] and not raw["hasWholeLocalFiveMetricPassingCandidate"]
    compare_fast_to_full(stem,best["apex"])

@pytest.mark.parametrize("stem",SUPPORTED[:2])
def test_original_boundary_moments_fixed_nodes_and_complete_outside_cells_are_preserved(stem):
    best=report(stem)["bestGeometryAndParetoCandidate"]
    audit=O.preservation_audit(doc(stem),best["apex"])
    assert audit["fixedPointCoordinatesExactlyUnchanged"]
    assert audit["allOutsideCentresAndVolumesExactlyUnchanged"]
    assert audit["fixedBoundaryFaces"]==len(doc(stem)["children"])
    assert audit["maximumFixedFaceAreaDifferenceM2"]==0
    assert audit["maximumFixedFaceCentreDifferenceM"]==0
    assert audit["relativeParentVolumeDifference"]<1e-10
    assert audit["parentFirstMomentDifferenceM4"]<1e-10
    for group in audit["sourceBoundaryPieceGroups"]:
        assert group["areaVectorDifferenceM2"]==0
        assert group["areaAmountDifferenceM2"]==0
        assert group["areaFirstMomentDifferenceM3"]==0

def test_no_meaningful_improvement_is_not_reported_as_a_success():
    raw=report("rank6-parent9316")
    assert raw["bestGeometryAndParetoCandidate"] is None
    assert not raw["hasWholeLocalFiveMetricPassingCandidate"]
    assert raw["minimumRelativeDeterminantImprovement"]==1e-8

def test_real_selection_excludes_certified_height_impossible_parents_and_binds_input_bytes():
    selected=json.loads((D/"selection.json").read_text())
    excluded=set(selected["certifiedQuadParentsExcluded"])
    excluded.update(row["parent"] for row in selected["certifiedTriangleParentsExcluded"])
    assert {652847,652920,652956}<=excluded
    for row in selected["selected"]:
        path=D/row["input"]
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row["inputSha256"]
        assert row["parent"] not in excluded
    assert len(selected["supportedSearchRepresentatives"])==3
    assert len(selected["coupledStateRefusals"])==3


@pytest.mark.parametrize("stem",SUPPORTED)
def test_fixed_triangle_bound_uses_actual_selected_face_branch_without_claiming_feasibility(stem):
    import audit_air_tetra_all_selected_bound as B
    raw=json.loads((D/"fixed-base-necessary-bound-audit.json").read_text())
    row=next(r for r in raw["rows"] if r["parent"]==doc(stem)["parent"] and r["rank"]==doc(stem)["rank"])
    assert row["qualifiedFixedBaseBadTets"]
    fixture=doc(stem)
    for cell in row["qualifiedFixedBaseBadTets"]:
        fs=fixture["cells"][str(cell["cell"])]["faces"]
        selected=[f for f in fs if fixture["faces"][str(f)]["patch"] is None
                  or fixture["faces"][str(f)]["patch"]["type"] in O.COUPLED]
        assert selected==cell["selectedFaces"]
        assert len(fs)==4 and len(selected)==cell["selectedCount"]
        assert fixture["apexWrittenPoint"] not in fixture["faces"][str(cell["fixedBaseFace"])]["pointIDs"]
        checked=B.fixed_triangle_bound(cell["fixedBaseCoordinates"],len(selected))
        assert checked["exactUpperBoundFraction"]==cell["bound"]["exactUpperBoundFraction"]
        assert not checked["necessaryFails001"]
        assert checked["necessaryOnlyNotFeasibility"]
