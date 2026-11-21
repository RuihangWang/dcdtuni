"""Incomplete components may prove a local failure, never complete acceptance."""
import copy, importlib.util, json, sys
import pytest
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "tools"))
spec = importlib.util.spec_from_file_location("serial_roof", root / "tools/audit_air_native_roof_serial.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fixture = root / "docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/roof-native-components-serial/transition-roof-components-rank0.jsonl"

def rows():
    return [json.loads(line) for line in fixture.read_text().splitlines()]

def test_complete_proposals_establish_real_local_warp_in_partial_star():
    result = m.local_trial(rows()[1])
    assert result["allProposalPacketsExported"]
    assert not result["allStarCellsExported"]
    assert result["localPlanarityRejectionEstablished"]
    assert result["exportedOriginalGeometry"]["uniqueNonplanarNativeFaceIds"] == []
    assert 1417 in result["exportedLocalTrial"]["uniqueNonplanarNativeFaceIds"]
    witness = next(w for w in result["independentLocalPlanarityRejectionWitnesses"] if w["nativeFace"] == 1417)
    assert witness["planarityErrorM"] > 1e5 * witness["planarityToleranceM"]
    assert sum(any(v != 0 for v in delta) for delta in witness["exactDeltaM"]) == 1
    assert abs(witness["signedQuadScalarTripleM3"]) > 1e-7
    assert not result["wholeComponentQualityAccepted"]

def test_partial_proposals_are_not_applied_even_to_visible_complete_cells():
    result = m.local_trial(rows()[2])
    assert not result["allProposalPacketsExported"]
    assert "exportedLocalTrial" not in result
    assert not result["wholeComponentQualityAccepted"]

def test_removing_one_packet_invalidates_a_previous_local_geometry_claim():
    row = copy.deepcopy(rows()[1])
    row["packets"].pop()
    result = m.local_trial(row)
    assert "exportedLocalTrial" not in result
    assert "Missing packets" in result["notEvaluated"]


def explicit_point_deltas():
    row = copy.deepcopy(rows()[1])
    prop = m.proposals(row)
    for p in row["points"]:
        p["deltaFromCompleteComponent"] = True
        p["finalProposalDelta"] = prop["byPoint"].get(p["point"], [{"delta": [0, 0, 0]}])[0]["delta"]
    row["totalPackets"] += 1
    return row

def test_complete_point_delta_map_can_prove_local_rejection_without_all_packets():
    result = m.local_trial(explicit_point_deltas())
    assert not result["allProposalPacketsExported"]
    assert result["allExportedPointsHaveCompleteComponentDelta"]
    assert result["localPlanarityRejectionEstablished"]
    assert not result["wholeComponentQualityAccepted"]

def test_one_missing_point_delta_prevents_partial_packet_trial():
    row = explicit_point_deltas()
    row["points"][0].pop("finalProposalDelta")
    result = m.local_trial(row)
    assert "exportedLocalTrial" not in result

def test_recorded_full_delta_must_match_visible_proposal():
    row = explicit_point_deltas()
    node = row["packets"][0]["pointProposals"][0]["point"]
    next(p for p in row["points"] if p["point"] == node)["finalProposalDelta"] = [1, 0, 0]
    with pytest.raises(ValueError, match="disagrees"):
        m.local_trial(row)


def test_global_conflict_outside_sample_prevents_arbitrary_delta_trial():
    row = explicit_point_deltas()
    row["exactConflictPoints"] = 1
    result = m.local_trial(row)
    assert "exportedLocalTrial" not in result
    assert "outside the sample" in result["notEvaluated"]


def v2_rows():
    path = fixture.parent.parent / "roof-native-components-serial-v2/transition-roof-components-rank0.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]

@pytest.mark.parametrize("record", [0, 1, 2])
def test_each_real_native_v2_trigger_is_independently_reproduced(record):
    row = v2_rows()[record]
    result = m.native_witness(row)
    key = "exactConflictReproduced" if row["vetoReason"] == 0 else "exactNativeGateFailureReproduced"
    assert result[key]

def test_actual_cell_plane_trigger_is_one_ring_coplanar_and_unmoved():
    result = m.native_witness(v2_rows()[2])
    assert not result["triggerCellBelongsToFullStar"]
    for p in result["triggerPointCoordinatesAndCompleteComponentDeltas"]:
        assert p["finalProposalDelta"] == [0, 0, 0]
    assert result["originalAndTrial"]["trial"]["faceToOtherCentreCosine"] == 0
    assert result["originalAndTrial"]["original"]["faceToOtherCentreCosine"] == 0
