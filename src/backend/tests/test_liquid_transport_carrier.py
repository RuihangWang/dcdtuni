"""Schema controls and opt-in real React->runner carrier stage; no native mesh job."""
import json
import os
from pathlib import Path
import shutil
import pytest
from pydantic import ValidationError
from backend.aircooling.runner.cfdrun_schema import GeometryExport, SourceRegionBinding
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def row(**changes):
    data = dict(stlFileId="stl_000", solidName="transport_a", sourceEntityUid="pipe-a",
        sourceRegionUid="project/pipe-a/stl-region/transport_a", sourceFacetCount=10,
        sourceRegionSha256="a" * 64, operation="liquidTransportUnionBoundary",
        carrierUid="project/liquid-transport/carrier/v1", carrierContributorUids=["pipe-a", "tee-b"])
    data.update(changes)
    return data


def geometry(first=None, second=None, path="merged_liquid_transport.stl"):
    rows = {"stl_000:transport_a": first or row()}
    if second:
        rows["stl_000:transport_b"] = second
    return {"stlFiles": {"stl_000": {"path": path, "kind": "grouped_ascii"}}, "sourceRegions": rows}


def test_explicit_shared_transport_carrier_does_not_replace_region_source_uid():
    result = GeometryExport.model_validate(geometry())
    owned = result.sourceRegions["stl_000:transport_a"]
    assert owned.sourceEntityUid == "pipe-a"
    assert owned.carrierUid != owned.sourceEntityUid
    assert owned.carrierContributorUids == ["pipe-a", "tee-b"]


@pytest.mark.parametrize("changes", [
    {"carrierUid": None}, {"carrierContributorUids": None},
    {"carrierContributorUids": ["tee-b"]}, {"carrierContributorUids": ["tee-b", "pipe-a"]},
    {"carrierContributorUids": ["pipe-a", "pipe-a"]},
    {"operation": "modelBoundary"},
])
def test_rejects_missing_ambiguous_or_unreviewed_carrier_metadata(changes):
    with pytest.raises(ValidationError):
        SourceRegionBinding.model_validate(row(**changes))


def test_rejects_split_open_regions_as_independent_carrier_files():
    with pytest.raises(ValidationError, match="whole closed carrier file"):
        GeometryExport.model_validate(geometry(path="pipe_a.stl"))


def test_rejects_same_carrier_uid_with_different_contributors():
    second = row(solidName="transport_b", sourceRegionUid="project/tee-b/stl-region/transport_b",
        sourceEntityUid="tee-b", carrierContributorUids=["pipe-a", "pipe-c", "tee-b"])
    with pytest.raises(ValidationError, match="aliases different"):
        GeometryExport.model_validate(geometry(second=second))


@pytest.mark.skipif(not os.environ.get("DCDTUNI_TRANSPORT_STAGE_FIXTURE"), reason="requires actual React-exported fixture")
def test_real_export_carrier_stays_whole_and_is_declared_as_wall_by_actual_runner(tmp_path):
    source = Path(os.environ["DCDTUNI_TRANSPORT_STAGE_FIXTURE"])
    case = tmp_path / "actual-react-stage"
    tri = case / "Mesh" / "constant" / "triSurface"
    tri.mkdir(parents=True)
    model = case / "model"
    model.mkdir()
    shutil.copy2(source / "transport.cfdrun", model / "transport.cfdrun")
    for stl in source.glob("*.stl"):
        if stl.name.startswith("actual-"):
            continue
        shutil.copy2(stl, tri / stl.name)
    original = (tri / "merged_liquid_transport.stl").read_bytes()
    runner = STLGenRunner(case_dir=case, cfdrun_path=model / "transport.cfdrun")
    runner._verify_hybrid_source_stls()
    runner._load()
    assert (tri / "merged_liquid_transport.stl").read_bytes() == original
    payload = json.loads((model / "transport.cfdrun").read_text())
    expected = [r["solidName"] for r in payload["geometryExport"]["sourceRegions"].values()]
    assert set(runner._stl_solids["merged_liquid_transport.stl"]) == set(expected)
    assert len(list(tri.glob("*liquid_transport*.stl"))) == 1
    system = case / "Mesh" / "system"
    system.mkdir(exist_ok=True)
    runner._generate_snappy_hex_mesh_dict(system)
    text = (system / "snappyHexMeshDict").read_text()
    assert "merged_liquid_transport.stl" in text
    assert "name merged_liquid_transport;" in text
    assert "    merged_liquid_transport\n    {" in text
    for name in expected:
        assert f"{name} {{ name {name}; }}" in text
        assert name in text
    # Actual wall patch declaration is the native carves=true entry point;
    # reading STL bytes or an emitter catalogue alone is insufficient.
    carrier_block = text[text.index("    merged_liquid_transport\n    {"):]
    assert "patchInfo { type wall; inGroups (wall); }" in carrier_block
    assert "faceType internal" not in carrier_block.split("//", 1)[0]
