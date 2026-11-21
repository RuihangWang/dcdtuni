"""Real compact trace regression: local validity never implies whole-parent acceptance."""
from pathlib import Path
import json,sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
TOOLS=ROOT/"tools"
sys.path.insert(0,str(TOOLS))
from audit_air_cone_candidate_independent import Stencil,evaluate
from audit_air_cone_union_candidate_search import score
BASE=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady"
UNION=BASE/"conforming-cone-skinny-unions-independent"

def read(path):
    return json.loads(path.read_text())

def test_parent9885_actual_full_stencil_and_preserved_outside_failure():
    doc=read(BASE/"conforming-cone-parent9885-independent/actual-context.json")
    result=evaluate(doc,doc["candidate"]);s=result["summary"]
    assert len(result["children"])==35 and len(result["faces"])==92
    assert s["determinantFailures"]==s["weightFailures"]==s["ratioFailures"]==s["skewFailures"]==0
    assert s["minimumD"]>.0084 and s["minimumWeight"]>.094
    assert s["childVolumeRelativeError"]<1e-10 and s["firstMomentDeltaFromOriginalParent"]<1e-15
    assert s["closedEdgesAndOppositeOrientation"] and s["concavityPairTriggerCount"]==0
    assert s["minimumBestSharedBasePointTetQuality"]>1e-5
    assert s["maxExternalCellVolumeDelta"]<1e-12 and s["maxExternalCellCentreDeltaM"]<1e-12
    assert any(v["cell"]==521112 and v["D"]<.001 for v in result["outsideCells"])
    assert s["externalCellsWithUnchangedExistingDeterminantFailure"]==[
        {"cell":521112,"D":pytest.approx(.0005638417378606792,rel=1e-10)}]

@pytest.mark.parametrize("parent,other",[(25147,507881),(25148,507899)])
def test_coplanar_minimal_union_bad_weight_is_not_removed_by_shared_apex_motion(parent,other):
    doc=read(UNION/f"parent{parent}-minimalUnion-context.json")
    row=next(r for r in read(UNION/"coplanar-weight-invariance.json")["rows"]
             if r["parent"]==parent and r["other"]==other)
    assert row["coplanarBaseErrorM"]==0
    assert row["invariantWeight"]==pytest.approx(.02358908504589,abs=1e-14)
    heights=[]
    for sample in row["samples"]:
        m=Stencil(doc,sample["apex"]);heights.append(sample["apex"][2])
        assert m.face_detail(row["face"])["weight"]==pytest.approx(row["invariantWeight"],abs=1e-11)
        assert sample["minimumKernelSlackM"]>3e-6
    assert max(heights)>min(heights)

@pytest.mark.parametrize("parent",[25147,25148])
def test_real_refan_candidate_conserves_original_geometry_but_full_parent_still_fails(parent):
    receipt=read(UNION/"refan-five-metric-candidates.json")
    row=next(r for r in receipt["candidates"] if r["parent"]==parent)
    doc=read(UNION/row["input"]);m=Stencil(doc,row["candidate"])
    result=evaluate(doc,row["candidate"]);s=result["summary"]
    local=score(doc,row["candidate"],True)
    assert local["minimumD"]>.001 and local["minimumWeight"]>.05
    assert local["minimumRatio"]>.01 and local["maximumSkew"]<4 and local["maximumNonOrthDegrees"]<70
    assert s["childVolumeRelativeError"]<1e-10 and s["firstMomentDeltaFromOriginalParent"]<1e-15
    assert s["maxParentBoundaryAreaVectorRelativeError"]<1e-10
    assert s["maxParentBoundaryAreaAmountRelativeError"]<1e-10
    assert s["maxParentBoundaryCentreErrorM"]<1e-12
    assert s["closedEdgesAndOppositeOrientation"] and s["concavityPairTriggerCount"]==0
    assert s["minimumChildCentroidPyramid"]>0 and s["maximumVertexOutsideM"]<1e-12
    assert s["minPolygonEdgeSupportM"]>=-1e-12 and s["maxAffectedFacePlanarityM"]<1e-12
    assert s["minimumCentreFanTetQuality"]>1e-30 and s["minimumBestSharedBasePointTetQuality"]>1e-30
    assert s["maxExternalCellVolumeDelta"]<1e-12 and s["maxExternalCellCentreDeltaM"]<1e-12
    assert doc["fixedOriginalCoordinatesBitExact"] and doc["allApexIncidentCellsExactChildren"]
    for k,value in doc["points"].items():
        if int(k)!=doc["apexWrittenPoint"]:
            assert np.array_equal(m.points[int(k)].view(np.uint64),np.asarray(value).view(np.uint64))
    assert s["determinantFailures"]>0 and s["weightFailures"]>0

@pytest.mark.parametrize("parent",[30503,46851])
def test_local_quad_union_is_not_falsely_promoted_to_whole_parent(parent):
    row=next(r for r in read(UNION/"minimal-union-five-metric-candidates.json")["candidates"] if r["parent"]==parent)
    doc=read(UNION/row["input"]);local=score(doc,row["candidate"],True)
    assert local["minimumD"]>.001 and local["minimumWeight"]>.05 and local["minimumRatio"]>.01
    assert local["maximumSkew"]<4 and local["maximumNonOrthDegrees"]<70
    result=evaluate(doc,row["candidate"]);s=result["summary"]
    assert s["closedEdgesAndOppositeOrientation"] and s["concavityPairTriggerCount"]==0
    assert s["childVolumeRelativeError"]<1e-10
    assert s["determinantFailures"]>0 and s["weightFailures"]>0

def test_three_to_two_refan_is_rejected_for_real_neighbour_volume_ratio():
    doc=read(UNION/"parent25147-refanThreeToTwo-context.json")
    result=evaluate(doc,doc["originalApex"])
    assert result["summary"]["minimumRatio"]<.01
    assert result["summary"]["ratioFailures"]>0
