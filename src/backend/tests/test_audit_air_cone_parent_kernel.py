"""Actual trace-backed kernel experiment, reproducible from closed-polygon fixture."""
from pathlib import Path
import importlib.util,json
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("remaining",ROOT/"tools/audit_air_cone_remaining_quality.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
DATA=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-parent9885-kernel-audit.json"
def rebuild(row,apexid,point):
    # Source polygons keep their original outline. Only the new internal apex
    # is substituted, then the single-cell winding is selected from raw Sf.
    points=[];faces=[];faceindices={}
    for f in row["faces"]:
        xyz=np.array(f["points"],dtype=float)
        original=m.g.Geometry.from_arrays(xyz,[list(range(len(xyz)))])
        reverse=np.dot(original.face(0)[0],f["outwardAreaVector"])<0
        for i,v in enumerate(f["vertexIds"]):
            if v==apexid:xyz[i]=point
        if reverse:xyz=xyz[:1].tolist()+xyz[:0:-1].tolist();xyz=np.asarray(xyz)
        start=len(points);points.extend(xyz.tolist());faces.append(list(range(start,start+len(xyz))));faceindices[f["face"]]=len(faces)-1
    return m.g.Geometry.from_arrays(points,faces),faceindices
def test_actual_parent_mapping_and_kernel_only_motion_are_real():
    d=json.loads(DATA.read_text());best=d["results"]["gridBest"]
    assert len(d["childrenMap"])==35
    assert all(r["inputStageParent"]==9885 for r in d["childrenMap"])
    assert d["nativeApexPoint"]>=d["originalSourcePointCount"]
    assert len(d["crossParentOutsideNeighbourCells"])==33
    assert best["minimumOriginalParentPlaneMarginM"]>3e-6
    assert best["geometry"]["allNonApexWrittenCoordinatesUnchanged"]
    assert best["geometry"]["allOriginalBaseFaceCoordinatesUnchanged"]
def test_maximum_plane_radius_is_not_mesh_quality_acceptance():
    d=json.loads(DATA.read_text());old=d["results"]["original"];lp=d["results"]["chebyshev"]
    assert len(old["failedDeterminants"])==2 and len(lp["failedDeterminants"])==3
    assert 498448 not in {r["cell"] for r in old["failedDeterminants"]}
    assert 498448 in {r["cell"] for r in lp["failedDeterminants"]}
    assert not lp["failedVolumeRatios"] and lp["failedWeights"]
    assert lp["normalizedMinimumQuality"]<1.
def test_closed_polygons_reproduce_all_children_and_actual_external_neighbours():
    d=json.loads(DATA.read_text());baseline=d["results"]["original"]
    for name in ["original","chebyshev","gridBest","axisKernelCenter","kernelXYParentBBoxZ"]:
        expected=d["results"][name];point=expected["point"]
        geos={row["cell"]:rebuild(row,d["writtenApexPoint"],point) for row in baseline["geometricCells"]+d["externalNeighbourOriginalGeometry"]}
        moment=np.zeros(3);volume=0.;det=[];weights=[];ratios=[];skew=[]
        for row in baseline["geometricCells"]:
            geo,indices=geos[row["cell"]];_,areas,_,cc,v,*_=geo.cell(0)
            selected=[a for f,a in zip(row["faces"],areas) if f["patch"] is None]
            det.append(m.determinant(selected)[0]);volume+=v;moment+=v*cc
        for f in expected["faces"]:
            owner,indices=geos[f["owner"]];sf,fc,*_=owner.face(indices[f["face"]]);oc=owner.cell(0)
            nc=geos[f["neighbour"]][0].cell(0) if f["neighbour"] is not None else None
            metric=m.face_metrics(sf,fc,oc[3],oc[4],owner.points[owner.ids(indices[f["face"]])],nc[3] if nc else None,nc[4] if nc else None)
            skew.append(metric["skewness"])
            if nc is not None:weights.append(metric["weight"]);ratios.append(metric["volumeRatio"])
        assert volume==pytest.approx(expected["cellVolumeSum"],rel=1e-13)
        assert moment==pytest.approx(expected["firstMomentSum"],rel=1e-13,abs=1e-16)
        assert min(det)==pytest.approx(expected["minimumDeterminant"],rel=1e-6,abs=1e-13)
        assert min(weights)==pytest.approx(expected["minimumWeight"],rel=1e-12)
        assert min(ratios)==pytest.approx(expected["minimumVolumeRatio"],rel=1e-12)
        assert max(skew)==pytest.approx(expected["maximumSkewness"],rel=1e-11)
    assert d["results"]["gridBest"]["normalizedMinimumQuality"]>1.

def test_axis_kernel_centre_is_derived_and_all_true_planes_are_rechecked():
    d=json.loads(DATA.read_text());box=d["derivedAxisKernelBounds"];value=d["results"]["axisKernelCenter"]
    assert value["point"]==pytest.approx((np.array(box["lower"])+np.array(box["upper"]))/2)
    assert value["minimumOriginalParentPlaneMarginM"]>3e-6
    assert not value["failedDeterminants"] and not value["failedWeights"] and not value["failedVolumeRatios"] and not value["failedSkewness"]
    assert box["candidateOnlyActual17PlaneGuardStillRequired"]
    assert d["outsideUnchangedLowDeterminantCells"][0]["cell"]==521112
    assert d["outsideUnchangedLowDeterminantCells"][0]["determinant"]<.001
