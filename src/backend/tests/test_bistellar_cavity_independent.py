"""One geometry-valid but quality-rejected original-node cavity."""
from pathlib import Path
import copy,json,sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_bistellar_cavity_independent as b
D=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/bistellar-cavity-independent"

@pytest.fixture(scope="module")
def raw():
    return json.loads((D/"rank1-first-cavity-complete-stencil.json").read_text())

@pytest.fixture(scope="module")
def result(raw):return b.analyze(raw)

def test_exact_original_nodes_intersection_and_partition_identity(raw,result):
    assert result["pointsUnchanged"]
    for key in ("tau","beta","gamma","remaining"):
        assert result["exactIntersection"][key]==raw["selected"]["exactIntersection"][key]
    assert result["exactIntersection"]["strictInterior"]
    assert result["exactVolumeSumOld"]==result["exactVolumeSumNew"]==raw["selected"]["oldTotalVolumeExact"]
    assert result["exactFirstMomentOld"]==result["exactFirstMomentNew"]==raw["selected"]["oldFirstMomentExact"]
    assert result["exactNewTetVolume"]==raw["selected"]["newVolumeExact"]

def test_original_six_outer_facets_and_all_complete_outside_cells(result):
    assert len(result["originalOuterShell"])==6
    assert all(r["originalCyclePatchZoneBaseUnchanged"] and r["outsideNeighbourUnchanged"] for r in result["originalOuterShell"])
    assert len(result["completeOutsideCells"])==6
    assert all(r["centreBitExact"] and r["volumeExact"] for r in result["completeOutsideCells"])

def test_exact_outward_original_cavity_halfspaces(result):
    assert len(result["exactOriginalCavityVertexHalfspaces"])==6
    assert all(r["passes"] for r in result["exactOriginalCavityVertexHalfspaces"])
    assert result["geometryPass"]

def test_original_quality_cannot_be_replaced_by_geometry_pass(result):
    assert result["old"]["Dfail"]==1 and result["new"]["Dfail"]==3
    assert result["old"]["Nfail"]==0 and result["new"]["Nfail"]==4
    assert result["new"]["Dmin"]==pytest.approx(1.027937294249e-7,rel=1e-10)
    assert result["new"]["Nmax"]==pytest.approx(85.11894705719,abs=1e-9)
    assert not result["allFiveLocalQualityAndRoleInheritancePass"]

def test_new_cells_inherit_removed_original_passing_cell_roles(result):
    rows=[r for r in result["passingRoleRegressions"] if r["metric"]=="D"]
    assert len(rows)==3
    assert all(r["originalPassingRoles"]==[671013] for r in rows)
    assert all(sorted(v)==[525057,671013] for v in result["cellLineage"].values())

def test_new_internal_faces_and_original_outer_faces_inherit_passing_roles(result):
    rows=[r for r in result["passingRoleRegressions"] if r["metric"]=="nonOrthogonalityDegrees"]
    assert len(rows)==4
    assert sorted(r["replacement"] for r in rows if r["replacement"] in (1499182,1773690))==[1499182,1773690]
    assert sum(r["originalPassingRoles"]==[1499187] for r in rows)==2

def test_canonical_owner_neighbour_flip_is_geometrically_equivalent(result):
    row=result["canonicalOwnerNeighbourAudit"]
    assert row["facesFlipped"]==[1499185,1499186,1773692,1773693]
    assert row["allOwnerLabelsBelowNeighbour"] and row["pointZeroRetained"]
    assert row["maximumAreaVectorOppositeErrorM2"]==0
    assert row["maximumCellCentreDifferenceM"]==0
    assert row["maximumCellRelativeVolumeDifference"]==0
    assert row["maximumFaceCentreDifferenceM"]<=2e-16
    assert max(row["maximumFamilyQualityDifference"].values())<1e-12

def test_duplicate_or_degenerate_new_tetrahedron_rejected(raw):
    bad=copy.deepcopy(raw)
    bad["selected"]["newTetPointIDs"][0][0]=bad["selected"]["newTetPointIDs"][0][1]
    with pytest.raises(ValueError,match="distinct"):b.rebuild(bad)

def test_changed_original_outer_shell_cannot_pass(raw):
    bad=copy.deepcopy(raw)
    bad["selected"]["newTetPointIDs"][0][2]=60947
    with pytest.raises(ValueError,match="six-facet"):b.rebuild(bad)

def test_protected_original_shared_face_is_rejected(raw):
    bad=copy.deepcopy(raw)
    next(r for r in bad["faces"] if r["face"]==1499187)["sourceZone"]=7
    with pytest.raises(ValueError,match="protected"):b.rebuild(bad)

def test_frozen_raw_input_never_mutated(raw):
    before=copy.deepcopy(raw);b.analyze(raw);assert raw==before
