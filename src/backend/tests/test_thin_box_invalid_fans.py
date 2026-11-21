"""Regressions for actual frozen pre-writer fan refusal, without running meshing."""
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_thin_box_invalid_fans as audit
EVIDENCE=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/thin-box-invalid-fan-audit"
DOC=json.loads((EVIDENCE/"source-input.json").read_text())
REPORT=json.loads((EVIDENCE/"fan-reconstruction.json").read_text())
ROW=next(x for x in REPORT["rows"] if x["rank"]==1 and x["face"]==89728)

def test_actual_full_screen_and_counter_scope():
    out=audit.audit(DOC)
    assert out["allAliveAtLeastFourNodeFaces"]==11948055
    assert out["shortScreenFaces"]==200
    assert out["reconstructedCentreFallbackFaces"]==162
    assert out["reconstructedBadPieces"]==162
    assert out["rankReconstructedBadFaces"]=={"0":0,"1":9,"2":0,"3":29,"4":0,"5":0,"6":114,"7":10}
    assert out["nativeObservedPartitionFailures"]==165
    assert out["nativeObservedMinusReconstructed"]==0
    assert out["reconstructionCounterMatchesObserved"]
    assert out["reconstructedNonconvexNoAnchorFaces"]==3
    assert not out["fullCounterClosureClaim"]
    assert not out["finalFanIdentityDirectlyDumpedForTheseFaces"]

def test_actual_short_edge_is_one_ulp_not_a_physical_thin_strip():
    row=audit.classification(ROW["originalPoints"])
    assert row["shortestEdgeChangedAxes"]==[
        {"axis":1,"a":.990277777625,"b":.9902777776249999,"positiveFloatULPs":1}]
    assert row["minEdge"]==np.spacing(.990277777625)
    assert row["convexPreflight"] and row["convexPlan"] and row["collinear"]
    assert row["splitFace"] and not row["validVertexAnchors"]
    assert len(row["badCentreFanPieces"])==1

def test_actual_positive_of_triangle_still_fails_writer_absolute_floor():
    row=audit.classification(ROW["originalPoints"])
    triangle=row["badCentreFanPieces"][0]
    assert triangle["OFarea"]>0
    assert triangle["writerNewellArea"]<1e-16
    assert triangle["writerAreaRefusal"]
    assert not audit.writable(triangle["points"])
    assert audit.WRITER_AREA_MIN==1e-12

def test_all_actual_reconstructed_refusals_remain_below_unchanged_floor():
    failures=[x for x in REPORT["rows"] if x["badCentreFanPieces"]]
    assert len(failures)==162
    for x in failures:
        assert x["convexPlan"] and x["collinear"]
        assert not x["validVertexAnchors"]
        assert not x["qualityRefanChangedInNativeWitness"]
        assert all(p["writerNewellArea"]<=audit.WRITER_AREA_MIN
                   and p["writerAreaRefusal"] for p in x["badCentreFanPieces"])
        assert all(d["positiveFloatULPs"] in (1,2)
                   for d in x["shortestEdgeChangedAxes"])

def test_tiny_edge_does_not_invent_split_on_retained_quad():
    quad=next(x for x in REPORT["rows"] if x["arity"]==4)
    out=audit.classification(quad["originalPoints"])
    assert out["minEdge"]<1e-15
    assert out["convexPlan"] and not out["collinear"]
    assert not out["splitFace"]
    assert not out["badCentreFanPieces"]

def test_exact_canonical_cycle_keeps_coordinates_and_survives_rotation_reversal():
    p=ROW["originalPoints"];canonical=audit.canonical_points(p)
    assert len(canonical)==len(p)
    assert {tuple(x) for x in canonical}=={tuple(x) for x in p}
    for reverse in (False,True):
        q=p[::-1] if reverse else p
        for k in range(len(q)):
            assert np.array_equal(audit.canonical_points(q[k:]+q[:k]),canonical)

def test_writer_gate_is_strict_and_finite_without_short_edge_tolerance():
    assert audit.writable([[0,0,0],[1,0,0],[0,1,0]])
    assert not audit.writable([[0,0,0],[2e-6,0,0],[0,1e-6,0]])  # area exactly 1e-12
    assert not audit.writable([[0,0,0],[1,0,0],[0,float("nan"),0]])
    with pytest.raises(ValueError,match="nonfinite"):
        audit.canonical_points([[0,0,0],[1,0,0],[0,float("nan"),0]])

def test_saved_native_quality_change_prevents_unproved_counter_attribution():
    doc={**DOC,"qualityChangedFaces":DOC["qualityChangedFaces"]+[{"rank":1,"face":89728}]}
    out=audit.audit(doc)
    assert out["reconstructedCentreFallbackFaces"]==161
    assert out["reconstructedBadPieces"]==161
    assert out["nativeObservedMinusReconstructed"]==1
    assert not out["reconstructionCounterMatchesObserved"]
    assert not out["fullCounterClosureClaim"]

def test_snapshot_identity_guard_rejects_label_or_point_changes(tmp_path):
    pts=np.asarray([[0,0,0],[1,0,0],[1,1,0],[0,1,0]],dtype="<f8")
    prefix=tmp_path/"conforming-source-rank0-"
    manifest={"format":"conforming-input-stage/v1","rank":0,"littleEndian":True,
              "labelBytes":4,"scalarBytes":8,"points":4,"faces":1,"facePointLabels":4}
    Path(str(prefix)+"manifest.json").write_text(json.dumps(manifest))
    for suffix,array in [
        ("points.f64",pts),("face-offsets.u64",np.array([0,4],dtype="<u8")),
        ("face-points.i32",np.arange(4,dtype="<i4")),("face-alive.u8",np.array([1],dtype="u1"))]:
        array.tofile(Path(str(prefix)+suffix))
    snap={"rank":0,"allEligibleFaces":1,"shortFaces":[{"face":0,"labels":[0,1,2,3],"points":pts.tolist()}]}
    assert audit.verify_snapshot(tmp_path,snap)["allSavedLabelsAndPointsMatch"]
    # one ULP on a normal number: a denormal (nextafter(0,1)) reads as 0 once
    # an earlier library in the session switched on flush-to-zero
    changed=json.loads(json.dumps(snap));changed["shortFaces"][0]["points"][1][0]=float(np.nextafter(1.,2.))
    with pytest.raises(ValueError,match="snapshot/source mismatch"):audit.verify_snapshot(tmp_path,changed)

def test_three_actual_nonconvex_outlines_have_no_original_anchor():
    rows=[x for x in REPORT["rows"] if not x["fromShortEdgeScreen"]]
    assert {(x["rank"],x["face"]) for x in rows}=={(3,35379),(3,35501),(6,1857488)}
    assert [x["arity"] for x in rows]==[9,9,13]
    for x in rows:
        assert not x["convexPreflight"] and not x["convexPlan"]
        assert x["collinear"] and x["splitFace"]
        assert not x["validVertexAnchors"]
        assert x["nonconvexNoAnchorFailure"]
        assert x["minEdge"]>.01  # different failure from the ULP micro-edge pieces
        assert not x["presentInNativePartitionWitness"]
        assert not x["qualityRefanChangedInNativeWitness"]
        assert x["sourcePlanarityErrorM"]<1e-13
    assert rows[2]["sourceInterface"]["patch"]==26  # preserves original index, no inferred BC

def test_three_no_anchor_refusals_are_exactly_simple_not_broken_outlines():
    out=audit.audit(DOC)
    assert len(out["nonconvexExactOutlineOracles"])==3
    assert all(x["exactBinaryPlanarAndSimple"] for x in out["nonconvexExactOutlineOracles"])
    for row in DOC["additionalSourceFaces"]:
        oracle=audit.signed.outline_oracle(row["points"])
        assert oracle["projectedArea"]!=0
        assert len(oracle["xyz"])==len(row["points"])

def test_prior_point_membership_is_exact_and_does_not_snap_one_ulp():
    points=np.array([[-1.,0.,0.],[2.,3.,4.],[2.,3.,4.]])
    queries=np.array([[2.,3.,4.],[2.,np.nextafter(3.,4.),4.],[-1.,0.,0.]])
    assert audit.coordinate_membership(points,queries)==[True,False,True]
    with pytest.raises(ValueError,match="finite xyz"):
        audit.coordinate_membership(points,[[float("nan"),0,0]])
