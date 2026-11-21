"""The actual one-parent prism candidate is rejected, despite good local D/W/R."""
from pathlib import Path
import copy,json,sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_axis_prism_extrusion as P
EVIDENCE=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/axis-prism-extrusion-independent"

@pytest.fixture(scope="module")
def doc():
    return json.loads((EVIDENCE/"rank3-parent618482-input.json").read_text())

@pytest.fixture(scope="module")
def trial(doc):
    return P.analyze(doc)

@pytest.fixture(scope="module")
def frontier_doc():
    return json.loads((EVIDENCE/"bounded-frontier-input.json").read_text())

def test_actual_conservation_and_original_points(doc,trial):
    assert len(doc["children"])==19 and len(doc["outside"])==14
    assert trial["candidateCells"]==8
    assert trial["originalSourceNodes"]==14 and trial["newPointCount"]==6
    assert trial["sourceNodesAllRetained"]
    original={np.asarray(x,dtype="<f8").tobytes() for x in doc["source"]["points"]}
    actual={np.asarray(x,dtype="<f8").tobytes() for cell in trial["newCells"] for face in cell["faces"] for x in face["coordinates"]}
    assert original<=actual
    assert abs(trial["volumeChangeAgainstWrittenFamily"])<1e-18
    assert abs(trial["volumeChangeAgainstSource"])<1e-18
    assert trial["firstMomentMaxAbsErrorAgainstWrittenFamily"]<1e-18
    for f in trial["exteriorFacetConservation"]:
        assert f["areaVectorMaxAbsError"]<1e-18
        assert f["areaMomentMaxAbsError"]<1e-18
        assert f["outsideActualCellSet"]==f["oldOutsideActualCellSet"]

def test_roles_and_outside_ids_are_never_coalesced(doc,trial):
    original={f["face"]:(f["patch"],f["zone"],f["role"]) for f in doc["source"]["faces"]}
    actual=[]
    for c in trial["newCells"]:
        for f in c["faces"]:
            if "sourceBase" in f:
                patch,zone,role=original[f["sourceBase"]]
                assert (f["sourcePatch"],f["sourceZone"])==(patch,zone)
                assert f["patch"]==(None if role is None else dict(name=role[0],type=role[1]))
                actual.append(f)
    assert {f["sourcePatch"] for f in actual if f["patch"]}=={151,152,153}
    bottom=[f for f in actual if f["sourceBase"]==740814]
    assert {f["other"] for f in bottom}=={687074,687075,687076}
    assert len(bottom)==5  # Distinct actual neighbors remain distinct facets.
    assert all(len({x["outside"] for x in doc["externalFaces"] if x["face"] in f["oldFaces"]})==1 for f in actual)

def test_actual_stock_coplanar_failures_are_not_hidden_by_good_metrics(trial):
    assert trial["minimumD"]>1e-3
    faces=trial["faceQuality"]
    assert len(faces)==33
    assert min(f["weight"] for f in faces if "weight" in f)>0.05
    assert min(f["volumeRatio"] for f in faces if "volumeRatio" in f)>0.01
    assert max(f["skewness"] for f in faces)<4
    assert max(f.get("nonorthogonality",0) for f in faces)<70
    assert all(c["geometry"]["boundaryEdgesPairExactly"] for c in trial["newCells"])
    assert min(c["geometry"]["minimumCentroidPyramidVolumeM3"] for c in trial["newCells"])>0
    assert trial["candidateConcavityFailures"]==2
    assert trial["outsideNewConcavityFailures"]==[175054,175134,466371,687074,687075]
    assert {c["id"] for c in trial["newCells"] if c["geometry"]["checkerTriggerCounts"]["coplanar"]}>={"new:0","new:4"}
    assert trial["acceptance"] is False
    assert trial["verdict"]=="REJECTED_OFFLINE_STOCK_FACE_CENTRE"

def test_modeled_interfaces_are_reverse_exact_but_frontier_is_not_claimed(trial):
    assert trial["modeledInterfacePairsExact"] and trial["modeledInterfacePairs"]==37
    assert len(trial["unmodeledNeighborInterfacesWithInsertedNodes"])==7
    assert {f["outsideUnmodeledCell"] for f in trial["unmodeledNeighborInterfacesWithInsertedNodes"] if f["outsideUnmodeledCell"] is not None}=={175135,175021,175133,687073}
    assert all(abs(r["volumeChange"])<1e-18 and max(abs(x) for x in r["momentChange"])<1e-17 for r in trial["outsideChanges"])

@pytest.mark.parametrize("field",["sourcePatch","sourceZone"])
def test_exterior_role_tamper_is_rejected(doc,field):
    d=copy.deepcopy(doc);d["externalFaces"][0][field]=9001
    with pytest.raises(ValueError,match="patch/zone identity"):P.construct(d)

def test_unresolved_source_physical_name_is_rejected(doc):
    d=copy.deepcopy(doc)
    f=next(x for x in d["externalFaces"] if x["patch"]);f["patch"]["name"]="wrong"
    with pytest.raises(ValueError,match="physical name/type"):P.construct(d)

@pytest.mark.parametrize("protected",["zone","coupled"])
def test_source_protected_role_is_rejected(doc,protected):
    d=copy.deepcopy(doc);f=d["source"]["faces"][0]
    if protected=="zone":f["zone"]=0
    else:f["role"]=["otherPair","processor"]
    with pytest.raises(ValueError,match="protected"):P.construct(d)

def test_missing_actual_neighbor_geometry_is_rejected(doc):
    d=copy.deepcopy(doc);d["outside"].pop(0)
    with pytest.raises(ValueError,match="outside cell missing"):P.construct(d)

def test_wrong_old_cell_values_are_rejected_before_using_them(doc):
    d=copy.deepcopy(doc);d["outside"][0]["volume"]*=2
    with pytest.raises(ValueError,match="cell V/M1"):P.construct(d)

def test_incomplete_original_facet_coverage_is_rejected(doc):
    d=copy.deepcopy(doc)
    first=next(i for i,f in enumerate(d["externalFaces"]) if f["sourceBase"]==719227)
    del d["externalFaces"][first]
    with pytest.raises(ValueError,match="cover original facet"):P.construct(d)

def test_nonfinite_original_coordinates_are_rejected(doc):
    d=copy.deepcopy(doc);d["source"]["points"][0][0]=float("nan")
    with pytest.raises(ValueError,match="finite"):P.construct(d)

def test_rational_clipping_has_no_epsilon_snap():
    F=P.F
    p=[(F(0),F(0),F(0)),(F(1),F(0),F(0)),(F(0),F(1),F(0))]
    q=P.clip(p,(F(1),F(0),F(0)),(F(1,3),F(0),F(0)))
    assert (F(1,3),F(0),F(0)) in q
    assert (F(1,3),F(2,3),F(0)) in q
    assert P.area(q)==(F(0),F(0),F(5,18))
    assert P.canon(q)==P.canon(q[1:]+q[:1])

def test_actual_five_parent_frontier_is_bounded_and_not_closed(frontier_doc):
    r=P.audit_frontier(frontier_doc)
    assert r["initialSourceParents"]==[239148,239231,239312,618043,618482]
    assert {x["parent"] for x in r["rows"] if x["axisBox"]}=={239148,239231,239312,618043}
    assert all(x["positiveDirectedClosed"] for x in r["rows"])
    assert r["frontierOutsideTheseFive"]==[239112,239149,239232,239311,239313,239394,618042]
    assert r["protectedRoleVetoRequired"] is False
    assert r["isClosedWithinTheseFive"] is False and r["acceptance"] is False

@pytest.mark.parametrize("role",["zone","coupled"])
def test_protected_whole_vertex_star_requires_veto(frontier_doc,role):
    d=copy.deepcopy(frontier_doc);f=d["parents"][0]["vertexStarFaces"][0]
    if role=="zone":f["zone"]=12
    else:f["patch"]=999;f["role"]=["otherPair","cyclic"]
    r=P.audit_frontier(d)
    assert r["protectedRoleVetoRequired"]
    assert r["acceptance"] is False

def test_incomplete_star_snapshot_is_rejected(frontier_doc):
    d=copy.deepcopy(frontier_doc);source=d["parents"][0]
    face=source["faces"][0]["face"]
    source["vertexStarFaces"]=[f for f in source["vertexStarFaces"] if f["face"]!=face]
    with pytest.raises(ValueError,match="missing from full vertex-star"):P.audit_frontier(d)

def test_repo_output_and_original_input_safety():
    with pytest.raises(ValueError,match="dcdtuni repository"):P.repo_path("/tmp/axis-prism-proof.json")
