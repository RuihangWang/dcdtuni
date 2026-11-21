"""Source-aware wind solves must not start on an unaccepted mesh."""
from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from types import SimpleNamespace
import shutil

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner, requires_hybrid_air_gate
from backend.tests.test_stlgen_source_receipt import _runner
from backend.tests.test_stlgen_hybrid_gate import _stub_runner


def _captured_check_result(*, returncode, stdout, stderr):
    """Mock only the owned launcher; real strict receipt validation still runs."""
    return SimpleNamespace(returncode=returncode,
                           communicate=lambda timeout: (stdout, stderr))


def test_source_aware_full_and_solve_only_paths_gate_before_solver(monkeypatch):
    runner, events = _stub_runner(monkeypatch)
    runner._data = {"runId": "source-aware", "meshConfig": {"meshEngine": "snappy"}}
    monkeypatch.setattr(runner, "_record_air_mesh_quality", lambda: events.append("record_quality"), raising=False)
    monkeypatch.setattr(runner, "_require_air_mesh_quality", lambda: events.append("require_quality"), raising=False)

    runner.run_simulation()
    assert events.index("_record_source_surface_mesh_source") < events.index("record_quality")
    assert events.index("record_quality") < events.index("require_quality") < events.index("_run_solve_from_mesh")
    events.clear()

    runner.run_solve_only()
    assert events.index("_verify_source_surface_mesh_source") < events.index("record_quality")
    assert events.index("record_quality") < events.index("require_quality") < events.index("_run_solve_from_mesh")


def test_mesh_only_records_verdict_after_source_receipt_without_solving(tmp_path, monkeypatch):
    runner, events = _stub_runner(monkeypatch)
    runner.mesh_dir = tmp_path / "Mesh"
    runner.mesh_dir.mkdir()
    runner._data = {"runId": "source-aware", "meshConfig": {"meshEngine": "snappy"}}
    monkeypatch.setattr(runner, "_ensure_watertight_room_enclosure", lambda: events.append("enclosure"), raising=False)
    monkeypatch.setattr(runner, "_clear_mesh_budget", lambda: None, raising=False)

    runner.run_mesh_only()

    assert events.index("_record_source_surface_mesh_source") < events.index("_record_air_mesh_quality")
    assert "_require_air_mesh_quality" not in events
    assert "_run_solve_from_mesh" not in events


def test_source_aware_rejected_quality_receipt_is_reported_not_refused(tmp_path, monkeypatch):
    """Owner rule (2026-10-22): mesh quality is reported, never required."""
    runner, _, _ = _runner(tmp_path)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "Popen", lambda *a, **k: _captured_check_result(
        returncode=0, stdout="Checking geometry...\nFailed 6 mesh checks.\n", stderr=""))

    runner._record_air_mesh_quality()
    receipt = json.loads((tmp_path / "mesh-quality-receipt.json").read_text())
    assert receipt["status"] == "rejected"
    assert receipt["failedChecks"] == 6
    runner._require_air_mesh_quality()
    assert runner._air_mesh_quality_status == "rejected"
    assert runner._accepted_air_mesh_sha == receipt["meshSha256"]


def test_source_aware_accepted_quality_receipt_is_bound_to_current_input(tmp_path, monkeypatch):
    runner, _, poly = _runner(tmp_path)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "Popen", lambda *a, **k: _captured_check_result(
        returncode=0, stdout="Mesh OK.\n", stderr=""))

    runner._record_air_mesh_quality()
    runner._require_air_mesh_quality()
    (poly / "faces").write_bytes(b"changed after acceptance")
    with pytest.raises(RuntimeError, match="strict mesh quality|identity"):
        runner._require_air_mesh_quality()


def test_hybrid_source_uids_also_emit_the_uniform_quality_receipt(tmp_path, monkeypatch):
    runner, raw, _ = _runner(tmp_path)
    raw["inputs"] = {"liquid": {"enabled": True}}
    raw["boundaryBindings"] = {"heat": {
        "objectPath": "constructions.racks.r1.servers.s1", "bcType": "nonfan_heat_flux",
    }}
    assert requires_hybrid_air_gate(raw)
    runner.cfdrun_path.write_text(json.dumps(raw))
    runner._verified_source_cfdrun_sha256 = hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "Popen", lambda *a, **k: _captured_check_result(
        returncode=0, stdout="Mesh OK.\n", stderr=""))

    runner._record_air_mesh_quality()

    assert (tmp_path / "mesh-quality-receipt.json").is_file()
    runner._require_air_mesh_quality()


def test_hybrid_without_source_uids_still_emits_rejected_mesh_quality(tmp_path, monkeypatch):
    runner, raw, _ = _runner(tmp_path)
    raw["runId"] = "hybrid_test"
    raw["geometryExport"]["sourceSurfaces"] = {}
    runner.cfdrun_path.write_text(json.dumps(raw))
    runner._verified_source_cfdrun_sha256 = hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "Popen", lambda *a, **k: _captured_check_result(
        returncode=0, stdout="Failed 6 mesh checks.\n", stderr=""))

    runner._record_air_mesh_quality()

    receipt = json.loads((tmp_path / "mesh-quality-receipt.json").read_text())
    assert receipt["status"] == "rejected"


def test_manifest_changed_after_stl_preflight_cannot_bind_old_mesh_to_new_uids(tmp_path, monkeypatch):
    runner, raw, _ = _runner(tmp_path)
    runner.tri_surface_dir = tmp_path / "Mesh/constant/triSurface"
    from backend.aircooling.runner import stl_geometry_index
    monkeypatch.setattr(stl_geometry_index, "verify_exported_stl_hashes",
                        lambda *a, **k: {"file_count": 1})
    runner._verify_hybrid_source_stls()

    changed = deepcopy(raw)
    changed["geometryExport"]["sourceSurfaces"]["stl_000:con_f"]["sourceObjectUid"] = "different_object"
    runner.cfdrun_path.write_text(json.dumps(changed))

    with pytest.raises(RuntimeError, match="manifest.*changed|input.*identity"):
        runner._record_source_surface_mesh_source()
    with pytest.raises(RuntimeError, match="manifest.*changed|input.*identity"):
        runner._record_air_mesh_quality()
    with pytest.raises(RuntimeError, match="manifest.*changed|input.*identity"):
        runner._load()
    assert not (tmp_path / "source-surface-mesh-source.json").exists()
    assert not (tmp_path / "mesh-quality-receipt.json").exists()


def test_solve_only_load_pins_source_manifest_without_rechecking_transformed_stls(tmp_path):
    runner, _, _ = _runner(tmp_path)
    runner._verified_source_cfdrun_sha256 = None
    runner.tri_surface_dir = tmp_path / "Mesh/constant/triSurface"
    # The existing mesh may have regrouped or removed the original uploaded
    # STL bytes; solve-only checks the source-to-mesh receipt instead.
    with pytest.raises(FileNotFoundError, match="No grouped STL files"):
        runner._load()
    assert runner._verified_source_cfdrun_sha256 == hashlib.sha256(
        runner.cfdrun_path.read_bytes()
    ).hexdigest()


def test_solve_only_rejects_stl_upload_after_mesh_before_load(tmp_path, monkeypatch):
    builder, _, _ = _runner(tmp_path)
    tri = tmp_path / "Mesh/constant/triSurface"
    tri.mkdir(parents=True, exist_ok=True)
    surface = tri / "merged_baffle.stl"
    surface.write_bytes(b"mesh-time derived STL")
    builder.tri_surface_dir = tri
    builder._record_source_surface_mesh_source()

    surface.write_bytes(b"another browser export, same manifest")
    runner = STLGenRunner(tmp_path, builder.cfdrun_path)
    monkeypatch.setattr(runner, "_load", lambda: pytest.fail(
        "stale source STL reached solve-only loader"))
    with pytest.raises(RuntimeError, match="STL.*(?:changed|snapshot|source)"):
        runner.run_solve_only()


def test_solve_only_rejects_uid_edit_before_mutating_existing_case(tmp_path, monkeypatch):
    builder, raw, _ = _runner(tmp_path)
    builder._record_source_surface_mesh_source()
    changed = deepcopy(raw)
    changed["geometryExport"]["sourceSurfaces"]["stl_000:con_f"]["sourceObjectUid"] = "m_99"
    builder.cfdrun_path.write_text(json.dumps(changed))
    runner = STLGenRunner(tmp_path, builder.cfdrun_path)
    monkeypatch.setattr(runner, "_load", lambda: pytest.fail(
        "changed source UID reached mutating solve-only load"))

    with pytest.raises(RuntimeError, match="source.*(?:identity|receipt|UID)"):
        runner.run_solve_only()


@pytest.mark.parametrize("when", ["before_copy", "after_copy"])
def test_source_aware_solve_rechecks_the_copied_mesh_before_solver_setup(tmp_path, monkeypatch, when):
    runner, _, poly = _runner(tmp_path)
    runner._data = {"meshConfig": {"meshEngine": "snappy"},
                    "simulationConfig": {"parallelSolve": False}}
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "Popen", lambda *a, **k: _captured_check_result(
        returncode=0, stdout="Mesh OK.\n", stderr=""))
    runner._record_air_mesh_quality()
    runner._require_air_mesh_quality()
    monkeypatch.setattr(runner, "_clear_mesh_budget", lambda: None)
    monkeypatch.setattr(runner, "_generate_solver_dicts",
                        lambda *a: pytest.fail("solver setup reached an unverified mesh"))

    original_copy = shutil.copytree

    def changed_copy(src, dest, *args, **kwargs):
        top_level = str(src) == str(runner.mesh_dir)
        if top_level and when == "before_copy":
            (poly / "faces").write_bytes(b"changed after accepted quality")
        result = original_copy(src, dest, *args, **kwargs)
        if top_level and when == "after_copy":
            (dest / "constant/polyMesh/faces").write_bytes(b"changed during copy")
        return result

    monkeypatch.setattr(shutil, "copytree", changed_copy)
    with pytest.raises(RuntimeError, match="mesh.*changed|mesh.*verified|quality.*identity"):
        runner._run_solve_from_mesh()


def test_direct_source_aware_solve_cannot_bypass_quality_requirement(tmp_path):
    runner, _, _ = _runner(tmp_path)
    runner._data = {"meshConfig": {"meshEngine": "snappy"}}
    runner._clear_mesh_budget = lambda: None
    with pytest.raises(RuntimeError, match="no verified mesh quality"):
        runner._run_solve_from_mesh()
    assert not (tmp_path / "log").exists()


def _hybrid_copy_runner(tmp_path):
    runner, raw, _ = _runner(tmp_path)
    raw["runId"] = "hybrid_test"
    raw["inputs"] = {"liquid": {"enabled": True}}
    raw["boundaryBindings"] = {"heat": {
        "objectPath": "constructions.racks.r1.servers.s1",
        "bcType": "nonfan_heat_flux",
    }}
    runner.cfdrun_path.write_text(json.dumps(raw))
    runner._verified_source_cfdrun_sha256 = hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    runner._data = {"meshConfig": {"meshEngine": "snappy"},
                    "simulationConfig": {"parallelSolve": False}}
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
    runner._hybrid_mesh_sha = mesh_identity_sha256(runner.mesh_dir)
    runner._accepted_air_mesh_sha = runner._hybrid_mesh_sha
    from backend.aircooling.runner.air_mesh_quality_receipt import record_prechecked_air_mesh_quality
    from backend.tests.test_air_mesh_quality_reuse import captured
    record_prechecked_air_mesh_quality(runner.mesh_dir, runner.cfdrun_path,
                                      **captured(runner.mesh_dir, runner.cfdrun_path))
    runner._clear_mesh_budget = lambda: None
    runner._generate_solver_dicts = lambda _path: None
    runner._resolve_solver_binary = lambda name: "mock-solver"
    def pretend_solve(_command, run_dir):
        result = run_dir / "100"
        result.mkdir()
        for name, field_class, dimensions, value in (
            ("T", "volScalarField", "[0 0 0 1 0 0 0]", "300"),
            ("U", "volVectorField", "[0 1 -1 0 0 0 0]", "(0.1 0 0)"),
        ):
            (result / name).write_text(
                f"FoamFile {{ class {field_class}; object {name}; }}\n"
                f"dimensions {dimensions};\ninternalField uniform {value};\n"
            )
    runner._run_cmd = pretend_solve
    runner._auto_harvest_sensors = lambda: None
    runner._write_convergence_verdict = lambda _run_dir, _log: None
    runner._hybrid_final_manifest = tmp_path / "hybrid-final.cfdrun"
    runner._hybrid_final_ledger = tmp_path / "hybrid-final-ledger.json"
    runner._hybrid_final_manifest.write_text(json.dumps(raw))
    runner._hybrid_final_ledger.write_text("{}")
    return runner, raw


def test_hybrid_root_manifest_copies_final_heat_inputs_to_run(tmp_path):
    runner, raw = _hybrid_copy_runner(tmp_path)
    assert requires_hybrid_air_gate(raw)
    assert not requires_hybrid_air_gate(runner._data)

    run_dir = runner._run_solve_from_mesh()

    assert (run_dir / "model" / runner._hybrid_final_manifest.name).is_file()
    assert (run_dir / runner._hybrid_final_ledger.name).is_file()


def test_hybrid_without_source_surfaces_checks_copied_mesh(tmp_path, monkeypatch):
    runner, raw = _hybrid_copy_runner(tmp_path)
    raw["geometryExport"]["sourceSurfaces"] = {}
    runner.cfdrun_path.write_text(json.dumps(raw))
    runner._verified_source_cfdrun_sha256 = hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    from backend.aircooling.runner.air_mesh_quality_receipt import record_prechecked_air_mesh_quality
    from backend.tests.test_air_mesh_quality_reuse import captured
    record_prechecked_air_mesh_quality(runner.mesh_dir, runner.cfdrun_path,
                                      **captured(runner.mesh_dir, runner.cfdrun_path))
    monkeypatch.setattr(runner, "_generate_solver_dicts",
                        lambda _path: pytest.fail("altered hybrid copy reached solver"))
    original_copy = shutil.copytree

    def corrupt_copy(src, dest, *args, **kwargs):
        result = original_copy(src, dest, *args, **kwargs)
        if str(src) == str(runner.mesh_dir):
            (dest / "constant/polyMesh/faces").write_bytes(b"changed during copy")
        return result

    monkeypatch.setattr(shutil, "copytree", corrupt_copy)
    with pytest.raises(RuntimeError, match="hybrid.*mesh|mesh.*hybrid"):
        runner._run_solve_from_mesh()
