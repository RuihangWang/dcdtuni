"""A persisted air-mesh verdict must bind the exact mesh and .cfdrun."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary")


def _quality_api():
    try:
        from backend.aircooling.runner.air_mesh_quality_receipt import (
            record_air_mesh_quality, verify_air_mesh_quality_receipt,
        )
    except ImportError:
        pytest.fail("air mesh quality receipt API is not implemented")
    return record_air_mesh_quality, verify_air_mesh_quality_receipt


def _case(root: Path, ranks: int = 0):
    mesh = root / "Mesh"
    roots = ([mesh / f"processor{rank}/constant/polyMesh" for rank in range(ranks)]
             if ranks else [mesh / "constant/polyMesh"])
    for rank, poly in enumerate(roots):
        poly.mkdir(parents=True)
        for name in MESH_FILES:
            (poly / name).write_bytes(f"rank={rank};{name}\n".encode())
        (poly / "owner").write_text(f'FoamFile {{ note "nCells:1"; object owner; }}\n// rank={rank}\n')
    cfdrun = root / "model" / "test.cfdrun"
    cfdrun.parent.mkdir()
    cfdrun.write_bytes(b'{"schemaVersion":2,"runId":"test"}\n')
    return mesh, cfdrun


def test_serial_receipt_requires_strict_mesh_ok_and_binds_input_bytes(tmp_path, monkeypatch):
    record, verify = _quality_api()
    mesh, cfdrun = _case(tmp_path)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256

    calls = []

    def completed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="Checking topology...\nMesh OK.\n", stderr="")

    monkeypatch.setattr(quality.subprocess, "run", completed)
    receipt = record(mesh, cfdrun, checkmesh_binary="checkMesh")

    assert calls == [["checkMesh", "-case", str(mesh.resolve()), "-constant",
                      "-allTopology", "-allGeometry"]]
    assert receipt["format"] == "air-mesh-quality/v1"
    assert receipt["status"] == "accepted"
    assert receipt["failedChecks"] == 0
    assert receipt["meshSha256"] == mesh_identity_sha256(mesh)
    assert receipt["cfdrunSha256"] == hashlib.sha256(cfdrun.read_bytes()).hexdigest()
    path = tmp_path / "mesh-quality-receipt.json"
    assert json.loads(path.read_text()) == receipt
    assert verify(path, mesh, cfdrun, require_accepted=True) == receipt


def test_parallel_failed_checks_are_recorded_as_rejected_even_with_exit_zero(tmp_path, monkeypatch):
    record, verify = _quality_api()
    mesh, cfdrun = _case(tmp_path, ranks=2)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality

    calls = []

    def completed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="Checking geometry...\nFailed 6 mesh checks.\n", stderr="")

    monkeypatch.setattr(quality.subprocess, "run", completed)
    receipt = record(mesh, cfdrun, checkmesh_binary="checkMesh", mpirun_binary="mpirun")

    assert calls == [["mpirun", "-np", "2", "checkMesh", "-case", str(mesh.resolve()),
                      "-constant", "-allTopology", "-allGeometry", "-parallel"]]
    assert receipt["status"] == "rejected"
    assert receipt["failedChecks"] == 6
    assert verify(tmp_path / "mesh-quality-receipt.json", mesh, cfdrun) == receipt
    with pytest.raises(ValueError, match="rejected|accepted"):
        verify(tmp_path / "mesh-quality-receipt.json", mesh, cfdrun, require_accepted=True)


@pytest.mark.parametrize("failure", ["mutated_mesh", "missing_verdict", "command_failure"])
def test_no_receipt_if_mesh_changes_or_checkmesh_lacks_valid_verdict(tmp_path, monkeypatch, failure):
    record, _ = _quality_api()
    mesh, cfdrun = _case(tmp_path)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality

    def completed(command, **kwargs):
        if failure == "mutated_mesh":
            (mesh / "constant/polyMesh/owner").write_bytes(b"changed")
        return SimpleNamespace(returncode=1 if failure == "command_failure" else 0,
                               stdout="Mesh OK.\n" if failure != "missing_verdict" else "Checking geometry...\n",
                               stderr="")

    monkeypatch.setattr(quality.subprocess, "run", completed)
    with pytest.raises(ValueError, match="changed|verdict|failed"):
        record(mesh, cfdrun, checkmesh_binary="checkMesh")
    assert not (tmp_path / "mesh-quality-receipt.json").exists()


def test_receipt_verifier_rejects_mesh_or_cfdrun_drift(tmp_path, monkeypatch):
    record, verify = _quality_api()
    mesh, cfdrun = _case(tmp_path)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="Mesh OK.\n", stderr=""))
    record(mesh, cfdrun, checkmesh_binary="checkMesh")
    path = tmp_path / "mesh-quality-receipt.json"

    (mesh / "constant/polyMesh/faces").write_bytes(b"changed mesh")
    with pytest.raises(ValueError, match="mesh|identity"):
        verify(path, mesh, cfdrun)
    (mesh / "constant/polyMesh/faces").write_bytes(b"rank=0;faces\n")
    cfdrun.write_bytes(b"changed input")
    with pytest.raises(ValueError, match="cfdrun|input|identity"):
        verify(path, mesh, cfdrun)


def test_failed_recheck_revokes_a_previous_accepted_receipt(tmp_path, monkeypatch):
    record, verify = _quality_api()
    mesh, cfdrun = _case(tmp_path)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    monkeypatch.setattr(quality.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="Mesh OK.\n", stderr=""))
    record(mesh, cfdrun, checkmesh_binary="checkMesh")
    path = tmp_path / "mesh-quality-receipt.json"
    assert verify(path, mesh, cfdrun, require_accepted=True)["status"] == "accepted"

    monkeypatch.setattr(quality.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="checkMesh crashed"))
    with pytest.raises(ValueError, match="failed"):
        record(mesh, cfdrun, checkmesh_binary="checkMesh")
    with pytest.raises(ValueError, match="missing"):
        verify(path, mesh, cfdrun, require_accepted=True)


def test_other_solver_settings_rebind_the_same_meshs_check_without_running_it(tmp_path, monkeypatch):
    # A receipt binds the .cfdrun too; a solve with another end time found it stale and ran checkMesh again on the
    # same mesh (Alibaba-hongchou: 98 s). The output it was made from now binds to the new input.
    record, verify = _quality_api()
    mesh, cfdrun = _case(tmp_path, ranks=2)
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    calls = []
    monkeypatch.setattr(quality.subprocess, "run", lambda command, **kw: (calls.append(command), SimpleNamespace(
        returncode=0, stdout="Checking geometry...\nFailed 2 mesh checks.\n", stderr=""))[1])
    first = record(mesh, cfdrun, checkmesh_binary="checkMesh", mpirun_binary="mpirun")
    assert len(calls) == 1 and first["status"] == "rejected"
    cfdrun.write_bytes(b'{"schemaVersion":2,"runId":"test","simulationConfig":{"endTime":30}}\n')
    path = tmp_path / "mesh-quality-receipt.json"
    with pytest.raises(ValueError):
        verify(path, mesh, cfdrun)
    again = quality.rebind_air_mesh_quality(mesh, cfdrun)
    assert len(calls) == 1                                     # no checkMesh
    assert again["cfdrunSha256"] == hashlib.sha256(cfdrun.read_bytes()).hexdigest()
    assert (again["status"], again["failedChecks"], again["meshSha256"]) == ("rejected", 2, first["meshSha256"])
    assert verify(path, mesh, cfdrun) == again
    # another mesh: no rebind
    (mesh / "processor1/constant/polyMesh/points").write_bytes(b"moved\n")
    assert quality.rebind_air_mesh_quality(mesh, cfdrun) is None
    # an output that does not hash to the receipt's: no rebind
    (mesh / "processor1/constant/polyMesh/points").write_bytes(b"rank=1;points\n")
    saved = json.loads((tmp_path / "mesh-quality-checkmesh.json").read_text())
    saved["stdout"] = "Mesh OK.\n"
    (tmp_path / "mesh-quality-checkmesh.json").write_text(json.dumps(saved))
    assert quality.rebind_air_mesh_quality(mesh, cfdrun) is None
