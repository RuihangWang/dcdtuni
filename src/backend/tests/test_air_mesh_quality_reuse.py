"""Legacy solve reuse must authorize exact full-scope geometry as well."""
import hashlib
from pathlib import Path
import pytest
from backend.aircooling.runner import air_mesh_quality_receipt as quality
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def case(tmp_path):
    mesh = tmp_path / "Mesh"
    poly = mesh / "constant/polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text('FoamFile { note "nCells:1"; }\n')
    run = tmp_path / "project.cfdrun"
    run.write_text('{"schemaVersion": 2}')
    return mesh, run


def captured(mesh, run, **overrides):
    result = dict(command=["checkMesh", "-allTopology", "-allGeometry"], workdir=mesh,
                  mesh_sha256_before=mesh_identity_sha256(mesh),
                  cfdrun_sha256_before=hashlib.sha256(run.read_bytes()).hexdigest(),
                  returncode=0, stdout="Mesh OK.\n", stderr="")
    result.update(overrides)
    return result


def test_prechecked_full_scope_reuses_output_without_second_subprocess(tmp_path, monkeypatch):
    mesh, run = case(tmp_path)
    monkeypatch.setattr(quality.subprocess, "run", lambda *a, **k: pytest.fail("duplicate checker"))
    receipt = quality.record_prechecked_air_mesh_quality(mesh, run, **captured(mesh, run))
    assert receipt["status"] == "accepted"
    assert receipt["checkmeshStdoutSha256"] == hashlib.sha256(b"Mesh OK.\n").hexdigest()
    assert receipt["checkmeshStderrSha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("override", [
    {"command": ["checkMesh", "-allTopology"]},
    {"command": ["echo", "-allTopology", "-allGeometry"]},
    {"command": ["mpirun", "-np", "2", "checkMesh", "-allTopology", "-allGeometry", "-parallel"]},
    {"returncode": 1}, {"stdout": "End\n"},
    {"mesh_sha256_before": "0" * 64}, {"cfdrun_sha256_before": "0" * 64},
])
def test_prechecked_rejects_wrong_scope_process_or_identity(tmp_path, override):
    mesh, run = case(tmp_path)
    with pytest.raises(ValueError):
        quality.record_prechecked_air_mesh_quality(mesh, run, **captured(mesh, run, **override))
    assert not (tmp_path / "mesh-quality-receipt.json").exists()


def runner(mesh, run):
    result = STLGenRunner.__new__(STLGenRunner)
    result.case_dir = mesh.parent
    result.mesh_dir = mesh
    result.cfdrun_path = run
    result._read_cfdrun_manifest = lambda: {"schemaVersion": 2}
    result._pinned_source_manifest = lambda: pytest.fail("legacy does not need provenance pin")
    result.calls = []
    def invoke(command, workdir):
        result.calls.append(command)
        result._prechecked_air_quality = captured(mesh, run)
        return result._prechecked_air_quality["stdout"]
    result._run_cmd_capture = invoke
    return result


def test_legacy_mesh_without_receipt_actually_checks_before_authorization(tmp_path):
    mesh, run = case(tmp_path)
    result = runner(mesh, run)
    result._require_air_mesh_quality()
    assert result.calls == ["checkMesh -allTopology -allGeometry"]
    assert result._accepted_air_mesh_sha == mesh_identity_sha256(mesh)
    result._require_air_mesh_quality()
    assert len(result.calls) == 1


def test_rejected_legacy_mesh_enters_the_solver_with_its_verdict_reported(tmp_path):
    """Owner rule (2026-10-22): mesh quality is reported, never required."""
    mesh, run = case(tmp_path)
    result = runner(mesh, run)
    checked = captured(mesh, run, stdout="Failed 5 mesh checks.\n")
    quality.record_prechecked_air_mesh_quality(mesh, run, **checked)
    result._require_air_mesh_quality()
    assert result._air_mesh_quality_status == "rejected"
    assert result.calls == []   # the bound record is reused, not checked again


def test_drifted_legacy_input_requires_new_actual_check(tmp_path):
    mesh, run = case(tmp_path)
    result = runner(mesh, run)
    result._require_air_mesh_quality()
    run.write_text('{"schemaVersion":2,"newInput":true}')
    result._require_air_mesh_quality()
    assert len(result.calls) == 2


def test_final_capture_with_changed_patch_metadata_is_rechecked(tmp_path):
    mesh, run = case(tmp_path)
    result = runner(mesh, run)
    result._prechecked_air_quality = captured(mesh, run)
    (mesh / "constant/polyMesh/boundary").write_text("changed patch type")
    result._require_air_mesh_quality()
    assert result.calls == ["checkMesh -allTopology -allGeometry"]


def test_direct_solve_entry_binds_the_quality_record_before_the_budget_clear(tmp_path):
    """A rejected verdict passes the binding step; the order (record, then budget) is kept."""
    mesh, run = case(tmp_path)
    result = runner(mesh, run)
    quality.record_prechecked_air_mesh_quality(
        mesh, run, **captured(mesh, run, stdout="Failed 5 mesh checks.\n"))

    class Reached(Exception):
        pass

    def stop(*_args, **_kwargs):
        assert result._air_mesh_quality_status == "rejected"
        raise Reached
    result._check_mesh_budget = stop
    with pytest.raises(Reached):
        result._run_solve_from_mesh()
    assert not (tmp_path / "log").exists()


def mock_completed_strict_quality(runner, monkeypatch):
    """A completed checker test double, retaining real receipt identity gates."""
    import shlex
    def capture(command, workdir):
        assert "-allTopology" in command and "-allGeometry" in command
        runner._prechecked_air_quality = captured(runner.mesh_dir, runner.cfdrun_path,
                                                command=shlex.split(command), workdir=workdir)
        return "Mesh OK.\n"
    monkeypatch.setattr(runner, "_run_cmd_capture", capture)


def test_prechecked_accepts_the_repo_built_checker(tmp_path):
    # dcCheckMesh (third_parties/dcCartMesh) prints checkMesh's own report
    mesh, run = case(tmp_path)
    binary = "/repo/third_parties/dcCartMesh/platforms/linux64GccDPInt32Opt/bin/dcCheckMesh"
    receipt = quality.record_prechecked_air_mesh_quality(
        mesh, run, **captured(mesh, run, command=[binary, "-allTopology", "-allGeometry"]))
    assert receipt["status"] == "accepted"


def test_prechecked_rejects_another_binary_named_like_a_checker(tmp_path):
    mesh, run = case(tmp_path)
    with pytest.raises(ValueError):
        quality.record_prechecked_air_mesh_quality(
            mesh, run, **captured(mesh, run, command=["checkMesh2", "-allTopology", "-allGeometry"]))


def test_resolver_prefers_the_repo_build_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "_REPO_PLATFORMS", tmp_path / "platforms")
    assert quality.resolve_checkmesh_binary() == "checkMesh"
    binary = tmp_path / "platforms/linux64GccDPInt32Opt/bin/dcCheckMesh"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    assert quality.resolve_checkmesh_binary() == "checkMesh"   # not executable
    binary.chmod(0o755)
    assert quality.resolve_checkmesh_binary() == str(binary)
    assert STLGenRunner._resolve_checkmesh_binary() == str(binary)
