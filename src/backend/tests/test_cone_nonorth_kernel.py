from functools import lru_cache
import json
import math
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_cone_nonorth_kernel as M
import audit_air_cone_quality_kernel as K
INPUT=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-quality-kernel/final-ratio-parents-input.json"


@lru_cache()
def doc():
    return next(v for v in json.loads(INPUT.read_text())["stencils"] if v["parent"]==9836)


@lru_cache()
def actual_report():
    return M.analyze(doc())


def test_same_parent_side_wedge_equals_true_n_angle_on_the_positive_branch():
    edge=np.array([0.,0.,1.]);d=np.array([1.,.2,.7])
    directions,meta=M.side_wedge_directions(edge,d,70)
    generator=np.random.default_rng(14582)
    for xy in generator.uniform(-2,2,size=(500,2)):
        sf=np.r_[xy,0.]
        cosine=np.dot(sf,d)/(np.linalg.norm(sf)*np.linalg.norm(d))
        linear=bool(np.all(directions@sf>=0))
        assert linear==(cosine>=math.cos(math.radians(70)))
    assert 0<meta["alpha"]<1 and meta["beta"]>0


def test_alpha_one_limit_must_retain_positive_longitudinal_branch():
    directions,_=M.side_wedge_directions([0,0,1],[1,0,0],0)
    assert np.all(directions@np.array([1.,0.,0.])>=0)
    assert np.any(directions@np.array([-1.,0.,0.])<0)
    assert np.any(directions@np.array([1.,.01,0.])<0)


@pytest.mark.parametrize("edge,d",[
    ([0,0,1],[0,0,1]),([0,0,1],[1,0,4]),([0,0,0],[1,0,0]),([0,0,1],[float("nan"),1,1])])
def test_invalid_or_unreachable_side_proposal_is_not_a_quality_authorization(edge,d):
    with pytest.raises(ValueError):M.side_wedge_directions(edge,d,70)


def test_external_inscribed_polygon_is_sufficient_and_deliberately_conservative():
    directions=M.external_inner_directions([0,0,1],70,16)
    generator=np.random.default_rng(42701)
    for d in generator.uniform(-4,4,size=(500,3)):
        if np.all(directions@d>=0):
            assert d[2]>0
            assert d[2]/np.linalg.norm(d)>=math.cos(math.radians(70))
    # A true circular-cone boundary point may be outside the inner polygon.
    boundary=np.array([0.,math.tan(math.radians(70)),1.])
    assert np.any(directions@boundary<0)
    assert np.all(directions@(boundary*np.array([.9,.9,1.]))>=0)


def test_every_original_internal_face_matches_the_full_stencil_cosine():
    model=M.ProposalModel(doc(),70-1e-5)
    actual=K.actual_stencil_quality(doc(),doc()["originalApex"])
    by_face={f["face"]:f for f in actual["faces"]}
    actual_cos=np.array([math.cos(math.radians(by_face[v["face"]]["nonOrthogonalityDegrees"]))
                         for v in model.entries])
    assert np.max(abs(model.proxy_cosines(np.zeros(3))-actual_cos))<2e-11
    assert sum(v["kind"]=="sameParentSide" for v in model.entries)==58
    assert sum(v["kind"]=="fixedExternalBase" for v in model.entries)==33
    assert not model.unsupported


def test_actual_lp_and_portable_clipping_candidates_clear_all_local_thresholds():
    report=actual_report()
    by_name={c["derivation"]:c for c in report["candidates"]}
    for name in ["linearMaxSlack","vertexMean","vertexBBoxMid"]:
        candidate=by_name[name]
        assert candidate["passesLocalGeometry"]
        assert candidate["passesLocalFiveMetricPareto"] and candidate["allLocalFiveMetricsPass"]
        assert len(candidate["actual"]["cells"])==36
        assert len(candidate["actual"]["faces"])==94
        assert candidate["relativeVolumeDifference"]<1e-10 and candidate["firstMomentDifference"]<1e-10


def test_improved_but_still_bad_determinant_candidates_are_not_called_all_pass():
    report=actual_report()
    candidate=next(v for v in report["candidates"] if v["derivation"]=="nearCurrent")
    assert candidate["passesLocalGeometry"] and candidate["passesLocalFiveMetricPareto"]
    assert candidate["actual"]["minimumDeterminant"]<.001
    assert not candidate["allLocalFiveMetricsPass"]


def test_coupled_remote_geometry_is_refused_before_partial_local_feasibility_can_be_claimed():
    raw=json.loads(json.dumps(doc()))
    face=raw["affectedFaces"][0]
    raw["faces"][str(face)]["neighbour"]=None
    raw["faces"][str(face)]["patch"]={"type":"cyclicAMI","name":"unsupported_test"}
    with pytest.raises(ValueError,match="coupled neighbour"):
        M.ProposalModel(raw,70)


def test_complete_geometry_report_serializes_for_the_cli_reproduction_path():
    report=actual_report()
    restored=json.loads(json.dumps(report))
    assert restored["candidates"][0]["childGeometryGateDetails"]
    assert restored["candidates"][0]["faceGeometryGateDetails"]


def test_six_actual_baseline_regressions_recompute_from_preserved_cell_coordinates():
    path=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-quality-nonorth-kernel/native-baseline-regression.json"
    raw=json.loads(path.read_text())
    assert len(raw["cells"])==6 and raw["sourceInitialComparisonExplainsEveryNewD"]
    geometry=K._load("audit_air_cone_candidate_independent.py")
    for row in raw["cells"]:
        for key in ["baselineCellGeometry","newCellGeometry"]:
            cell=row[key];points=dict(zip(cell["pointIDs"],cell["pointCoordinates"]))
            selected=[]
            for face in cell["facePolygons"]:
                patch=face["patch"]
                if patch is None or patch["type"] in {"cyclic","processor","processorCyclic"}:
                    sf,_=geometry.face_geometry(np.array([points[v] for v in face["pointIDs"]]))
                    selected.append(sf)
            area=np.array(selected);mean=np.linalg.norm(area,axis=1).mean()
            recomputed=abs(np.linalg.det((area/mean).T@(area/mean)))/8
            assert recomputed==pytest.approx(cell["cellDeterminant"],rel=2e-10,abs=1e-14)
        assert row["baselineActualD"]>=.001 and row["newActualD"]<.001
        assert row["newComparisonOriginalProfileD"]<.001
