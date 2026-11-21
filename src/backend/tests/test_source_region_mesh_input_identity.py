"""The source-region mesh receipt pins what built the mesh, not the heat a
liquid result later hands to the air run."""
from copy import deepcopy

from backend.aircooling.runner.source_region_catalogue import source_region_mesh_input_sha


def _raw():
    return {
        "runId": "run_2026-10-15T23:39:00.595Z",
        "sourceProject": {"uid": "proj_x", "schemaVersion": 4},
        "geometryExport": {
            "stlFiles": {"stl_010": {"path": "merged_server_8U.stl", "contentHash": "ab"}},
            "solids": {"sol_a": {"solidName": "a", "patchName": "a", "stlFileId": None,
                                 "objectPath": "constructions.racks.m_1.servers.e_2",
                                 "role": "wall", "areaM2": None}},
        },
        "meshConfig": {"meshEngine": "dcCart"},
        "meshGeometry": {"racks": {"m_1": {"servers": [{"type": "fanServer",
                                                        "physics": {"power": 2000}}]}}},
        "boundaryBindings": {"bb_a": {"patchName": "a", "bcType": "fan_server_outlet",
                                      "parameters": {"powerW": 2000, "temperatureC": 35}}},
        "inputs": {}, "sourceTerms": {},
    }


def test_hybrid_heat_handoff_keeps_the_mesh_input_identity():
    raw = _raw()
    handed = deepcopy(raw)
    handed["runId"] = "hybrid_713ec24ea02e6e9ea4c9"
    handed["boundaryBindings"]["bb_a"]["parameters"] = {"powerW": 1489.99}
    handed["meshGeometry"]["racks"]["m_1"]["servers"][0]["physics"]["power"] = 1489.99
    handed["geometryExport"]["solids"]["sol_a"].update(stlFileId="stl_010", areaM2=0.0427)
    assert source_region_mesh_input_sha(handed) == source_region_mesh_input_sha(raw)
    nonfan = deepcopy(raw)
    nonfan["boundaryBindings"]["bb_a"]["parameters"] = {"heatFluxWm2": 12.5}
    assert source_region_mesh_input_sha(nonfan) == source_region_mesh_input_sha(raw)


def test_mesher_rank_count_and_time_target_keep_the_mesh_input_identity():
    # Base604 meshed on 16 ranks; the reloaded editor exported meshProcess 8
    # and solve-only was refused. dcCart is rank-invariant and the mesh bytes
    # are pinned by meshSha256, so how the mesher ran is not mesh input.
    from backend.aircooling.runner.source_region_catalogue import (
        _mesh_input_sha, source_region_mesh_input_matches)
    raw = _raw()
    raw["meshConfig"].update(meshProcess=16, parallelMesh=True, meshTimeLimitSeconds=300)
    raw["meshGeometry"]["meshConfig"] = {"meshEngine": "dcCart", "meshProcess": 16}
    rerun = deepcopy(raw)
    rerun["meshConfig"].update(meshProcess=8, parallelMesh=False, meshTimeLimitSeconds=None)
    rerun["meshGeometry"]["meshConfig"]["meshProcess"] = 8
    assert source_region_mesh_input_sha(rerun) == source_region_mesh_input_sha(raw)
    # a receipt written before v3 still matches its own manifest
    assert source_region_mesh_input_matches(_mesh_input_sha(raw, "v2"), raw)
    assert not source_region_mesh_input_matches(_mesh_input_sha(raw, "v2"), rerun)


def test_geometry_and_roles_still_change_the_mesh_input_identity():
    raw = _raw()
    for edit in (
        lambda r: r["meshConfig"].update(meshEngine="snappy"),
        lambda r: r["geometryExport"]["solids"]["sol_a"].update(role="inlet"),
        lambda r: r["boundaryBindings"]["bb_a"].update(bcType="wall"),
        lambda r: r["meshGeometry"]["racks"]["m_1"]["servers"][0].update(type="server"),
        lambda r: r["meshConfig"].update(baseSize=0.2),
    ):
        changed = deepcopy(raw)
        edit(changed)
        assert source_region_mesh_input_sha(changed) != source_region_mesh_input_sha(raw)


def test_operating_values_do_not_change_the_mesh_input_identity():
    """v4: airflows, powers and temperatures are boundary values, not mesh input (owner 10-28: changing an ACU's
    airflow must not ask for a new mesh); a receipt written before v4 still matches its own manifest."""
    from backend.aircooling.runner.source_region_catalogue import (
        _mesh_input_sha, source_region_mesh_input_matches)
    raw = _raw()
    changed = deepcopy(raw)
    changed["boundaryBindings"]["bb_a"]["parameters"].update(velocity=1.0)
    assert source_region_mesh_input_sha(changed) == source_region_mesh_input_sha(raw)
    assert source_region_mesh_input_matches(_mesh_input_sha(raw, "v3"), raw)
    assert not source_region_mesh_input_matches(_mesh_input_sha(raw, "v3"), changed)



def test_editor_view_switches_do_not_change_the_mesh_input_identity():
    """v5: showing the locationInMesh marker or opening the background-block editor is saved with the mesh
    settings but meshes nothing; with them in, the editor asked to refresh a dcCart mesh after a click. A
    receipt written before v5 still matches its own manifest; a real setting still changes the identity."""
    from backend.aircooling.runner.source_region_catalogue import (
        _mesh_input_sha, source_region_mesh_input_matches)
    raw = _raw()
    raw["meshConfig"].update(locInMeshVisible=False, blockMeshEditActive=False, blockMeshMoveMode=False)
    shown = deepcopy(raw)
    shown["meshConfig"].update(locInMeshVisible=True, blockMeshEditActive=True, blockMeshMoveMode=True)
    assert source_region_mesh_input_sha(shown) == source_region_mesh_input_sha(raw)
    assert source_region_mesh_input_matches(_mesh_input_sha(raw, "v4"), raw)
    finer = deepcopy(raw)
    finer["meshConfig"]["meshBaseSize"] = 0.1
    assert source_region_mesh_input_sha(finer) != source_region_mesh_input_sha(raw)

def test_solve_only_receipt_fields_ignore_the_export_run_id():
    """A solve-only run re-exports the manifest (a new timestamped runId, and
    a hybrid_* one once the liquid heat is handed over); the design the mesh
    was built from is pinned by the project hash and the STL hashes."""
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    raw = _raw()
    raw["sourceProject"]["contentHash"] = "c" * 64
    resent = deepcopy(raw)
    resent["runId"] = "hybrid_713ec24ea02e6e9ea4c9"
    for hybrid in (True, False):
        assert (STLGenRunner._solve_source_fields(resent, hybrid) ==
                STLGenRunner._solve_source_fields(raw, hybrid))
    # a project edit that leaves the geometry alone (solver cores, a sensor,
    # a server's power) reuses the mesh; a changed STL does not
    changed = deepcopy(raw)
    changed["sourceProject"]["contentHash"] = "d" * 64
    for hybrid in (True, False):
        assert STLGenRunner._solve_source_fields(changed, hybrid) == STLGenRunner._solve_source_fields(raw, hybrid)
    changed = deepcopy(raw)
    changed["geometryExport"]["stlFiles"]["stl_010"]["contentHash"] = "ef"
    for hybrid in (True, False):
        assert STLGenRunner._solve_source_fields(changed, hybrid) != STLGenRunner._solve_source_fields(raw, hybrid)


def test_air_mesh_quality_verdict_reports_concave_cells_without_refusing():
    """Concave cells are a warning (the mesh gate's own rule); every other
    strict check still refuses."""
    from backend.aircooling.runner.air_mesh_quality_receipt import _verdict
    concave_only = (" ***Concave cells (using face planes) found, number of cells: 83293\n"
                    "Failed 1 mesh checks.\n")
    assert _verdict(concave_only) == ("accepted", 0)
    with_tets = (" ***Error in face tets: 4 faces with low quality or negative volume decomposition tets.\n"
                 " ***Concave cells (using face planes) found, number of cells: 83293\n"
                 "Failed 2 mesh checks.\n")
    assert _verdict(with_tets) == ("rejected", 1)
    assert _verdict("Mesh OK.\n") == ("accepted", 0)


def test_source_surface_solve_only_ignores_the_reexported_run_id(tmp_path):
    """The ordinary (non-hybrid) source-surface receipt is checked the same
    way: a solve-only re-export with a new runId reuses the mesh."""
    import json
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    raw = _raw()
    raw["sourceProject"]["contentHash"] = "c" * 64
    raw["geometryExport"]["sourceSurfaces"] = {"stl_010:a": {"sourceObjectUid": "m_1"}}
    resent = deepcopy(raw)
    resent["runId"] = "run_2026-10-16T15:52:04.888Z"
    runner = object.__new__(STLGenRunner)
    runner.case_dir = tmp_path
    runner.mesh_dir = tmp_path
    receipt = {"format": "source-surface-mesh-source/v2",
               **STLGenRunner._ordinary_source_fields(raw), "mesh_sha256": "m"}
    (tmp_path / "source-surface-mesh-source.json").write_text(json.dumps(receipt))
    runner._read_cfdrun_manifest = lambda: resent
    runner._pinned_source_manifest = lambda: resent
    runner._needs_source_surface_mesh_receipt = lambda _raw: True
    import backend.aircooling.runner.openfoam_patch_area as area
    original = area.mesh_identity_sha256
    area.mesh_identity_sha256 = lambda _mesh: "m"
    try:
        runner._verify_source_surface_mesh_source()
        # a project edit that leaves the geometry alone reuses the mesh
        edited = deepcopy(resent)
        edited["sourceProject"]["contentHash"] = "d" * 64
        runner._read_cfdrun_manifest = lambda: edited
        runner._pinned_source_manifest = lambda: edited
        runner._verify_source_surface_mesh_source()
        # a changed STL does not
        changed = deepcopy(resent)
        changed["geometryExport"]["stlFiles"]["stl_010"]["contentHash"] = "ef"
        runner._read_cfdrun_manifest = lambda: changed
        runner._pinned_source_manifest = lambda: changed
        import pytest
        with pytest.raises(RuntimeError, match="does not match"):
            runner._verify_source_surface_mesh_source()
    finally:
        area.mesh_identity_sha256 = original


def test_perforation_values_do_not_change_the_mesh_input_identity():
    """v6: a rack door's or a baffle's open area and hole size are porous-jump values, not mesh input (owner 10-28:
    changing a module's open ratio must not ask for a new mesh); Alibaba_hangchou's doors 64 -> 99 % were refused
    for solve-only. Whether the doors' perforations differ stays in: it decides one merged door patch or one per
    door. A receipt written before v6 still matches its own manifest."""
    from backend.aircooling.runner.source_region_catalogue import (
        _mesh_input_sha, source_region_mesh_input_matches)
    raw = _raw()
    perf = {"shape": "circle", "holeDiameterMm": 3, "openAreaPct": 64}
    raw["meshGeometry"]["racks"]["m_1"]["inletDoor"] = {"enabled": True, "perforated": dict(perf)}
    raw["meshGeometry"]["racks"]["m_2"] = {"servers": [], "inletDoor": {"enabled": True, "perforated": dict(perf)}}
    raw["meshGeometry"]["baffles"] = {"b_1": {"geometry": {"faces": {"front": {"patches": [
        {"id": "p_1", "type": "rect", "perforation": dict(perf), "physics": {"baffleType": "perforated"}}]}}}}}
    opened = deepcopy(raw)
    for rack in opened["meshGeometry"]["racks"].values():
        rack["inletDoor"]["perforated"].update(openAreaPct=99, holeDiameterMm=5)
    opened["meshGeometry"]["baffles"]["b_1"]["geometry"]["faces"]["front"]["patches"][0]["perforation"]["openAreaPct"] = 30
    assert source_region_mesh_input_sha(opened) == source_region_mesh_input_sha(raw)
    assert source_region_mesh_input_matches(_mesh_input_sha(raw, "v5"), raw)
    assert not source_region_mesh_input_matches(_mesh_input_sha(raw, "v5"), opened)
    split = deepcopy(raw)
    split["meshGeometry"]["racks"]["m_2"]["inletDoor"]["perforated"]["openAreaPct"] = 50
    assert source_region_mesh_input_sha(split) != source_region_mesh_input_sha(raw)
    typed = deepcopy(raw)
    typed["meshGeometry"]["baffles"]["b_1"]["geometry"]["faces"]["front"]["patches"][0]["physics"]["baffleType"] = "wall"
    assert source_region_mesh_input_sha(typed) != source_region_mesh_input_sha(raw)
