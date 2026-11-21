"""Offline semantic regressions of trace-bound geometry and role inheritance."""
from pathlib import Path
import copy
import json
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_cone_small_union_independent as u
DATA=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-small-union-independent"

@pytest.fixture(scope="module")
def raw():
    return json.loads((DATA/"p30430-complete-written-stencil.json").read_text())

@pytest.fixture(scope="module")
def results(raw):
    return [u.analyze(raw,[526902,526906]),
            u.analyze(raw,[526919,526920],join_faces=[1929316,1929317])]

@pytest.mark.parametrize("index",[0,1])
def test_actual_complete_geometry_and_five_local_metrics(results,index):
    row=results[index]
    assert row["passesCandidateGeometry"]
    assert row["passesAllFiveAbsoluteLocalQuality"]
    assert row["passesFullFamilyFiveMetricLineagePareto"]
    assert row["mergedCell"]["faceCentreConcavityPairTriggers"]==0
    assert row["directedEdges"]["allEdgesExactlyTwo"]
    assert row["directedEdges"]["allEdgesOppositelyDirected"]
    assert row["minimumCentreFanTetQuality"]>1e-30
    assert row["minimumSharedOriginalAnchorTetQuality"]>1e-30

@pytest.mark.parametrize("index",[0,1])
def test_full_outside_cells_recomputed_not_frozen(results,index):
    row=results[index]
    assert len(row["unchangedCompleteCells"])==177
    assert row["allOtherCompleteCellCentresAndVolumesUnchanged"]
    assert row["pointsNeverMoved"]
    assert row["fixedOuterPolygonsPatchZoneAndOutsideNeighbourPreserved"]
    assert row["relativeVolumeDifference"]<1e-10
    assert row["firstMomentDifferenceM4"]<1e-10

def test_same_physical_outline_independent_edge_cancel(results,raw):
    row=results[1]["joinedPhysicalOutline"]
    assert row["polygon"]==[576620,27752,576881,576508]
    original={v for f in row["originalFaces"] for v in raw["faces"][str(f)]["pointIDs"]}
    assert set(row["polygon"])==original
    assert row["removedCommonEdges"]==[[27752,576508]]
    assert row["vectorAreaRelativeError"]==0
    assert row["unsignedAreaRelativeError"]==0
    assert row["areaWeightedCentroidDifferenceM"]<1e-14
    assert row["geometryPreserved"]

def test_canonical_flip_preserves_point_zero_and_physics(results):
    row=results[0]["canonicalOwnerNeighbourAudit"]
    assert row["facesFlipped"]==[1502868]
    assert row["allOwnerLabelsBelowNeighbour"] and row["pointZeroRetained"]
    assert row["maximumAreaVectorOppositeErrorM2"]==0
    assert row["maximumFaceCentreDifferenceM"]==0
    assert row["maximumCellCentreDifferenceM"]==0
    assert row["maximumCellRelativeVolumeDifference"]==0
    assert row["unchangedPhysicalExteriorRolesAndPolygons"]
    assert max(row["maximumFamilyQualityDifference"].values())<1e-14

def test_original_raw_complete_cell_moments_are_reproduced(raw):
    doc=u.labelled_input(raw);m=u.WrittenStencil(doc,doc["originalApex"])
    for label,row in raw["cells"].items():
        _,_,cc,v=m.cell(int(label))
        assert np.array_equal(cc.view(np.uint64),np.asarray(row["CC"]).view(np.uint64))
        assert v==row["V"]

def test_numeric_label_reuse_cannot_hide_deleted_passing_cell():
    old={"cells":[{"cell":50,"D":.0005},{"cell":51,"D":.002}],"faces":[]}
    new={"cells":[{"cell":50,"D":.0006}],"faces":[]}
    row=u.lineage_pareto(old,new,[50,51],50)["D"]
    assert set(row["candidateBadLabels"])<=set(row["oldBadLabels"])
    assert row["originalPassingRoleFailures"][0]["originalPassingRoles"]==[51]
    assert not row["pass"]

def test_coalesced_face_inherits_both_original_passing_roles():
    old={"cells":[],"faces":[{"face":10,"skew":5.},{"face":11,"skew":.2}]}
    new={"cells":[],"faces":[{"face":10,"skew":4.5}]}
    row=u.lineage_pareto(old,new,[],0,{10:[10,11]})["skew"]
    assert row["badCountDoesNotIncrease"] and row["worstFailedSeverityDoesNotIncrease"]
    assert row["originalPassingRoleFailures"][0]["originalPassingRoles"]==[11]
    assert not row["pass"]

def test_nonfinite_replacement_metric_cannot_pass():
    old={"cells":[{"cell":1,"D":.002},{"cell":2,"D":.003}],"faces":[]}
    new={"cells":[{"cell":1,"D":float("nan")}],"faces":[]}
    assert not u.lineage_pareto(old,new,[1,2],1)["D"]["pass"]

@pytest.mark.parametrize("key,value",[("patch",{"name":"other","type":"patch"}),("sourceZone",4),("sourceBase",123)])
def test_physical_merge_rejects_mismatched_original_role(raw,key,value):
    doc=u.labelled_input(raw);merged,_,_=u.union_document(doc,[526919,526920])
    merged["faces"]["1929317"][key]=value
    with pytest.raises(ValueError,match="same physical patch"):
        u.coalesce_physical_faces(merged,[1929316,1929317],526919)

def test_affected_coupled_neighbor_is_not_treated_as_physical(raw):
    doc=u.labelled_input(raw)
    doc["faces"]["1928778"]["patch"]["type"]="processor"
    with pytest.raises(ValueError,match="coupled"):
        u.union_document(doc,[526902,526906])

def test_three_children_require_connected_internal_face_graph(raw):
    doc=u.labelled_input(raw);base={526902,526906}
    connected_neighbors={r["owner"] for r in doc["faces"].values() if r["neighbour"] in base}
    connected_neighbors|={r["neighbour"] for r in doc["faces"].values() if r["owner"] in base}
    distant=next(c for c in doc["children"] if c not in base|connected_neighbors)
    with pytest.raises(ValueError,match="face-connected"):
        u.union_document(doc,[*base,distant])

def test_shared_edge_orientation_mismatch_rejected(raw):
    doc=u.labelled_input(raw);merged,_,_=u.union_document(doc,[526919,526920])
    merged["faces"]["1929317"]["pointIDs"].reverse()
    with pytest.raises(ValueError,match="shared exact edge"):
        u.coalesce_physical_faces(merged,[1929316,1929317],526919)

def test_source_inputs_are_not_mutated(raw):
    before=copy.deepcopy(raw)
    u.analyze(raw,[526902,526906])
    u.analyze(raw,[526919,526920],join_faces=[1929316,1929317])
    assert raw==before

def test_keeps_existing_family_failures_and_does_not_claim_whole_mesh(results):
    for row in results:
        assert len(row["fullFamilyLineagePareto"]["D"]["candidateBadLabels"])==17
        assert len(row["fullFamilyLineagePareto"]["weight"]["candidateBadLabels"])==12
        assert len(row["fullFamilyLineagePareto"]["nonOrthogonalityDegrees"]["candidateBadLabels"])==1
        assert "Not native mesh acceptance" in row["scope"]
