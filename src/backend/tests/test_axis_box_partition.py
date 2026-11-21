"""Pure geometry guards and actual complete frozen boundary refusal."""
from pathlib import Path
import copy,importlib.util,json
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("axis_box",ROOT/"tools/audit_air_axis_box_partition.py")
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
DATA=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-thin-box-partition/source-input.json"

@pytest.fixture(scope="module")
def source():return json.loads(DATA.read_text())

@pytest.fixture(scope="module")
def result(source):return a.plan(source,652847)

def test_actual_source_seam_and_source_masks(source,result):
    assert result["derivedLongAxis"]==1 and result["derivedThinAxis"]==0
    assert result["originalSeam"] in {p[1] for f in source["parents"][0]["faces"] for p in f["points"]}
    assert result["singleParent"]["summary"]["cells"]==2
    assert result["jointParents"]["summary"]["cells"]==6
    assert all(c["selectedFaceCount"]==5 for c in result["jointParents"]["cells"])
    assert result["singleParent"]["summary"]["minDeterminant"]>.001
    # Physical Zlo is excluded; treating all six faces as selected is different.
    cell=result["singleParent"]["cells"][0]
    d=np.array(cell["hi"])-cell["lo"]
    sf=np.array([[d[1]*d[2],0,0],[-d[1]*d[2],0,0],[0,d[0]*d[2],0],
                 [0,-d[0]*d[2],0],[0,0,d[0]*d[1]],[0,0,-d[0]*d[1]]])
    assert a.q.determinant(sf)[0]>4*cell["determinant"]

def test_original_coordinates_area_moment_volume_are_preserved(result):
    joint=result["jointParents"]
    assert joint["allOriginalCoordinatesRemainUsed"]
    assert len(joint["points"])==24
    for parent in joint["parentConservation"]:
        assert parent["relativeVolumeError"]<1e-10
        assert parent["relativeFirstMomentError"]<1e-10
        assert all(f["relativeAreaError"]<1e-10 and f["relativeVectorAreaError"]<1e-10
                   and f["centroidDifferenceM"]<1e-10 for f in parent["boundaryFaces"])

def test_full_fragment_geometry_and_internal_metrics(result):
    x=result["jointParents"]
    assert all(c["allFacePolygonsPlanar"] and c["allFacePolygonsConvex"]
               and c["checkerTriggerCounts"]=={"coplanar":0,"positiveOutside":0,"nearPlanarMargin":0}
               and c["relativeClosure"]<1e-12 for c in x["cells"])
    z=x["summary"]
    assert z["minDeterminant"]>.001 and z["minInternalWeight"]>.05 and z["minInternalRatio"]>.01
    assert z["maxInternalSkewness"]<4 and z["maxInternalNonorthDegrees"]<70
    assert z["minTetQuality"]>1e-15

def test_actual_fixed_exterior_exposes_direct_splice_refusal(result):
    single=result["singleParent"]["retainedNeighbourDiagnostic"]
    joint=result["jointParents"]["retainedNeighbourDiagnostic"]
    assert single["minimumWeight"]<.05
    assert joint["minimumWeight"]>.05 and joint["minimumRatio"]>.01
    assert joint["maximumSkewness"]<4 and joint["maximumNonorthogonalityDegrees"]<70
    assert joint["coplanarBoxTriggerPairsWithDistinctOutsideRoles"]==4
    assert joint["cannotAuthorizeDirectSplice"]
    assert joint["oldOutsideFacesThatWouldBeSplit"]
    assert not any(x["passingToFailingD"] for x in joint["outsideDeterminantDiagnostics"])
    # Existing external failures remain, even though selected-area change is not worse.
    assert sum(x["oldD"]<.001 for x in joint["outsideDeterminantDiagnostics"])==3
    assert joint["maximumRelativeExteriorAreaCoverageError"]<1e-10

def test_warped_source_is_unsupported(source):
    p=copy.deepcopy(source["parents"][0])
    p["faces"][0]["points"][0][0]+=1e-12
    with pytest.raises(ValueError,match="exactly axis-planar"):a.source_box(p)

def test_overlap_source_tiles_are_unsupported(source):
    p=copy.deepcopy(source["parents"][0])
    p["faces"].append(copy.deepcopy(p["faces"][0]))
    with pytest.raises(ValueError,match="coverage incomplete"):a.source_box(p)

def test_original_seam_is_required_not_case_coordinate(source):
    peer=source["parents"][1]
    with pytest.raises(ValueError,match="no original interior seam"):
        a.choose_original_seam(peer,1)

def test_axis_permutation_preserves_derivation_and_quality(source,result):
    data=copy.deepcopy(source);perm=[2,0,1]
    def vec(v):return [v[i] for i in perm]
    for p in data["parents"]:
        for f in p["faces"]:
            f["points"]=[vec(v) for v in f["points"]];f["Sf"]=vec(f["Sf"]);f["centre"]=vec(f["centre"])
    for f in data["actualWrittenParentBoundary"]:
        f["points"]=[vec(v) for v in f["points"]];f["Sf"]=vec(f["Sf"]);f["centre"]=vec(f["centre"])
        if f["outsideCentre"] is not None:f["outsideCentre"]=vec(f["outsideCentre"])
    for c in data["actualOutsideCompleteCells"]:
        c["centre"]=vec(c["centre"])
        for f in c["faces"]:
            f["points"]=[vec(v) for v in f["points"]];f["Sf"]=vec(f["Sf"]);f["centre"]=vec(f["centre"])
    other=a.plan(data,652847)
    assert other["derivedLongAxis"]==2 and other["derivedThinAxis"]==1
    for key in ["minDeterminant","minInternalWeight","minInternalRatio","maxInternalSkewness"]:
        assert other["jointParents"]["summary"][key]==pytest.approx(result["jointParents"]["summary"][key],abs=1e-12)
    assert other["jointParents"]["allOriginalCoordinatesRemainUsed"]

def test_actual_primitive_concavity_witness_is_not_centroid_pyramid(result):
    d=result["jointParents"]["retainedNeighbourDiagnostic"]
    w=[x for x in d["installedCheckConcaveCellsWitnesses"] if x["differentOutsideRole"]]
    assert len(w)==4
    for row in w:
        n=np.asarray(row["firstOutwardSf"]);n/=np.linalg.norm(n)
        delta=np.asarray(row["otherFaceCentre"])-row["firstFaceCentre"]
        dot=float(delta@n/np.linalg.norm(delta))
        assert dot==row["actualUnitDot"] and dot>-1e-6
        assert row["exactInstalledPrimitivePredicateTrue"]
        assert row["cellCentreDoesNotEnterThisPredicate"]
    # A positive centroid pyramid is insufficient for this separate primitive gate.
    assert min(p["centroidPyramidVolumeM3"] for p in d["pieces"])>0
