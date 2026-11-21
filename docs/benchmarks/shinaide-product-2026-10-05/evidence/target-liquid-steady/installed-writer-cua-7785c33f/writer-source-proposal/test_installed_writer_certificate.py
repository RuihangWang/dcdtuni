"""Certificate semantics only; no C++ build/native acceptance is claimed."""
from __future__ import annotations
import copy
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("writer_certificate", ROOT / "tools/audit_air_writer_certificate.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
FIXTURES = ROOT / "docs/benchmarks/air-product-presets-2026-10-05/installed-writer-certificate-source-proposal/independent-writer-order-fixtures.json"

def hx(value):
    return struct.pack("=d", value).hex()

def bits(word):
    return struct.pack("=Q", word).hex()

def pair(record=None):
    writer = [hx(0.125), hx(0.25), hx(0.5), hx(0.01)]
    if record:
        writer = [bits(word) for word in record["writerBits"]]
    face = {
        "nativeFace": 41, "nativeOwner": 7, "nativeNeighbour": -1,
        "rawPatch": 3, "rawZone": -1, "relativeFace": 0,
        "compactFace": 55, "compactOwner": 6,
        "patchName": "procBoundary0to1", "patchType": "processor",
        "patchRole": "type processor;nFaces 1;myProcNo 0;neighbProcNo 1;transform unknown;",
        "nativePoints": [11, 12, 13], "pointAliases": [11, 12, 13],
        "ringAliases": [11, 12, 13], "compactPoints": [21, 22, 23],
        "coordinateBytes": [[hx(x) for x in row] for row in [(0., 0., 0.), (0., 1., 0.), (0., 0., 1.)]],
        "SfBytes": [hx(0.5), hx(0.), hx(0.)], "FCBytes": [hx(0.), hx(1/3), hx(1/3)],
        "CCBytes": writer[:3], "VBytes": writer[3], "zoneRoles": [["baffle", False]],
    }
    base = {"format": "installed-writer-coupled-certificate/v1", "status": "earliest-baseline", "rank": 0,
            "scalarBytes": 8, "byteOrder": sys.byteorder, "failures": 0, "baselineImmutable": True, "faces": [face],
            "coupledPatchRoles": {face["patchName"]: face["patchRole"]},
            "coupledPatchSizes": {face["patchName"]: 1}}
    actual = copy.deepcopy(base)
    actual["status"] = "local-pass-awaiting-global"
    return base, actual

def test_legal_compaction_changes_compact_labels_only():
    old, new = pair()
    new["faces"][0].update(compactFace=48, compactOwner=5, compactPoints=[18, 19, 20])
    assert MOD.compare_certificates(old, new)["passed"]

@pytest.mark.parametrize("index", range(4))
def test_four_actual_writer_order_fixtures_use_writer_bytes(index):
    record = json.loads(FIXTURES.read_text())["records"][index]
    assert record["sequentialWriterBeforeAfterExact"] and not record["sourceVsWriterExact"]
    old, new = pair(record)
    assert MOD.compare_certificates(old, new)["passed"]
    # A source-CF baseline is a different arithmetic oracle, even though
    # prewriter before/after equality held in this real compact fixture.
    old["faces"][0]["CCBytes"] = [bits(x) for x in record["sourceBits"][:3]]
    old["faces"][0]["VBytes"] = bits(record["sourceBits"][3])
    assert not MOD.compare_certificates(old, new)["passed"]

@pytest.mark.parametrize("field", [
    "nativeOwner", "rawPatch", "rawZone", "relativeFace", "patchName", "patchType",
    "patchRole", "zoneRoles", "pointAliases", "ringAliases", "coordinateBytes",
    "CCBytes", "VBytes", "SfBytes", "FCBytes",
])
def test_changed_identity_or_bytes_refused(field):
    old, new = pair()
    row = new["faces"][0]
    if field in ("nativeOwner", "rawPatch", "rawZone", "relativeFace"):
        row[field] += 1
    elif field in ("patchName", "patchType", "patchRole"):
        row[field] += "-changed"
    elif field == "zoneRoles":
        row[field][0][1] = True
    elif field in ("pointAliases", "ringAliases"):
        row[field][0] += 100
    elif field == "coordinateBytes":
        row[field][0][0] = hx(math.nextafter(0., 1.))
    elif field == "VBytes":
        row[field] = hx(math.nextafter(0.01, 1.))
    else:
        row[field][0] = hx(math.nextafter(0.125, 1.))
    assert not MOD.compare_certificates(old, new)["passed"]

def test_point0_rotation_is_refused_even_with_same_polygon():
    old, new = pair()
    for name in ("ringAliases", "compactPoints", "coordinateBytes"):
        new["faces"][0][name] = new["faces"][0][name][1:] + new["faces"][0][name][:1]
    assert not MOD.compare_certificates(old, new)["passed"]

def test_negative_zero_is_not_positive_zero():
    old, new = pair()
    new["faces"][0]["coordinateBytes"][0][0] = hx(-0.)
    assert not MOD.compare_certificates(old, new)["passed"]

@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_matching_nonfinite_bytes_still_refused(value):
    old, new = pair()
    old["faces"][0]["CCBytes"][0] = new["faces"][0]["CCBytes"][0] = hx(value)
    assert not MOD.compare_certificates(old, new)["passed"]

def test_missing_or_duplicate_coupled_face_refused():
    old, new = pair()
    new["faces"].append(copy.deepcopy(new["faces"][0]))
    assert not MOD.compare_certificates(old, new)["passed"]
    new["faces"] = []
    assert not MOD.compare_certificates(old, new)["passed"]

def test_updated_after_candidate_is_not_earliest_baseline():
    old, new = pair()
    old["status"] = "local-pass-awaiting-global"
    assert not MOD.compare_certificates(old, new)["passed"]

def test_native_or_io_failure_refused():
    old, new = pair()
    new["failures"] = 1
    assert not MOD.compare_certificates(old, new)["passed"]

def test_source_boundary_and_normal_mapping_are_explicit():
    source = (ROOT / "src/backend/aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells/dcCartCutCells.C").read_text()
    # This is source-bound coverage of lifecycle placement, not execution.
    assert 'if (!baselineOnly && !m.coneEvidenceParent.empty())' in source
    assert 'baselineOnly ? IOobject::NO_WRITE : IOobject::AUTO_WRITE' in source
    assert 'baselineOnly ? word("dcCartPreOperatorWriterOracle")' in source
    assert 'if (bindings)' in source and 'bindings->cell=newCell;bindings->point=newPoint;' in source
    gate = source[source.index("    mesh.addZones(pz, fz, cz);"):]
    assert "verifyWriterCoupled" in gate
    assert gate.count("returnReduce(localFailures+ioFailures") == 1
    assert "writeWriterCheckpoint(*m.writerBaseline->checkpoint" in gate
    assert "writerSameScalar" in source and "std::memcmp" in source

@pytest.mark.parametrize("field", ["neighbourPatch", "referPatch", "transform", "inGroups", "myProcNo", "neighbProcNo", "nFaces"])
def test_full_metadata_role_fields_are_exact(field):
    old, new = pair()
    new["coupledPatchRoles"]["procBoundary0to1"] += f"{field} changed;"
    assert not MOD.compare_certificates(old, new)["passed"]

def test_empty_rank_certificate_does_not_create_remote_quality():
    old, new = pair()
    old["faces"] = new["faces"] = []
    old["coupledPatchRoles"] = new["coupledPatchRoles"] = {}
    old["coupledPatchSizes"] = new["coupledPatchSizes"] = {}
    result = MOD.compare_certificates(old, new)
    assert result["passed"] and result["faces"] == 0 and not result["remoteQualityRecomputed"]

def test_empty_patch_metadata_is_still_certified():
    old, new = pair()
    old["faces"] = new["faces"] = []
    old["coupledPatchSizes"] = new["coupledPatchSizes"] = {"emptyCyclic": 0}
    old["coupledPatchRoles"] = {"emptyCyclic": "type cyclic;nFaces 0;neighbourPatch twin;transform coincidentFullMatch;"}
    new["coupledPatchRoles"] = {"emptyCyclic": "type cyclic;nFaces 0;neighbourPatch changed;transform coincidentFullMatch;"}
    assert not MOD.compare_certificates(old, new)["passed"]
