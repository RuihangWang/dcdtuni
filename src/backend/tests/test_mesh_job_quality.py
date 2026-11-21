"""Mesh jobs must expose the verdict of their final checkMesh pass."""

import importlib.util
import sys
from pathlib import Path

import pytest
from unittest.mock import Mock

from backend.aircooling.runner import polymesh_io
from backend.aircooling.runner.stlgen_runner import STLGenRunner


@pytest.fixture(scope="module")
def server():
    path = Path(__file__).resolve().parents[2] / "server" / "server.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("mesh_quality_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_failed_final_quality_is_visible_even_when_mesh_process_exits_zero(server):
    lines = [
        "snappyHexMesh: Mesh OK.",
        "Step 8/8: checkMesh (final quality check)",
        "Checking topology...",
        "Checking geometry...",
        " ***Max skewness = 5.1291, 4 highly skew faces detected",
        " ***Cells with small determinant (< 0.001) found, number of cells: 6",
        " ***Concave cells (using face planes) found, number of cells: 13363",
        "Failed 3 mesh checks.",
        "Mesh complete. Complete case in /tmp/Shinaide/Mesh",
    ]
    summary = server._summarize_mesh_run_quality(lines, 0)
    assert summary["checkMeshOk"] is False
    # concave cells are a warning, as in the runner's strict gate
    assert summary["failedChecks"] == 2
    assert summary["meshWarnings"]


def test_concave_cells_alone_do_not_fail_the_mesh(server):
    """A dcCart mesh always reports concave cut cells; the runner's strict gate
    accepts them (warning only), so the editor must not lock the solver tab."""
    lines = [
        "Step 8/8: checkMesh (final quality check)",
        "Checking topology...",
        "Checking geometry...",
        " ***Concave cells (using face planes) found, number of cells: 25553",
        "Failed 1 mesh checks.",
    ]
    summary = server._summarize_mesh_run_quality(lines, 0)
    assert summary["checkMeshOk"] is True
    assert summary["failedChecks"] == 0
    assert [w["code"] for w in summary["meshWarnings"]] == ["concave_cells"]


def test_repaired_mesh_uses_last_checkmesh_report(server):
    lines = [
        "Step 5/5: checkMesh (final quality check)",
        "Checking topology...",
        "Writing region 1 (fully disconnected) with 5 cells to cellSet region1",
        "Failed 1 mesh checks.",
        "Removed 5 disconnected/isolated cell(s) from mesh",
        "Checking topology...",
        "Number of regions: 1 (OK).",
        "Checking geometry...",
        "Mesh OK.",
    ]
    summary = server._summarize_mesh_run_quality(lines, 0)
    assert summary["checkMeshOk"] is True
    assert summary["failedChecks"] == 0
    assert summary["meshWarnings"] == []


def test_incomplete_final_quality_report_cannot_be_green(server):
    summary = server._summarize_mesh_run_quality(
        ["Step 5/5: checkMesh (final quality check)", "Checking topology..."], 0
    )
    assert summary["checkMeshOk"] is False
    assert summary["meshWarnings"]


def test_no_final_checkmesh_step_is_unverified(server):
    summary = server._summarize_mesh_run_quality(["snappyHexMesh: Mesh OK."], 0)
    assert summary["checkMeshOk"] is False
    assert summary["meshWarnings"][0]["code"] == "missing_final_checkmesh"


@pytest.mark.parametrize("lines,returncode,code", [
    (["Checking topology..."], 0, "incomplete_checkmesh"),
    (["[server] checkMesh failed: executable missing"], -1, "checkmesh_command_failed"),
])
def test_explicit_checkmesh_without_verdict_still_blocks_solve(server, lines, returncode, code):
    summary = server._summarize_check_mesh_result(lines, returncode)
    assert summary["checkMeshOk"] is False
    assert summary["meshWarnings"][0]["code"] == code
    assert summary["meshWarnings"][0]["level"] <= 2


def test_serial_cleanup_checks_the_repaired_mesh_before_reporting_quality(tmp_path, monkeypatch):
    instance = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model/p.cfdrun")
    instance._data = {"meshConfig": {"meshEngine": "dcCart"}}
    from backend.tests.test_air_nonempty_finite_preflight import _mesh
    _mesh(instance.mesh_dir, 1)
    monkeypatch.setattr(polymesh_io, "mesh_topology_summary", lambda *_: None)
    reports = iter([
        "Writing region 0 (fully disconnected) with 10000 cells to cellSet region0\n"
        "Writing region 1 (fully disconnected) with 5 cells to cellSet region1\n"
        "Failed 1 mesh checks.\n",
        "Number of regions: 1 (OK).\nMesh OK.\n",
    ])
    commands = []
    monkeypatch.setattr(instance, "_run_cmd_capture", lambda cmd, _: (commands.append(cmd), next(reports))[1])
    monkeypatch.setattr(instance, "_do_subset_mesh", Mock(return_value=5))
    assert instance._remove_disconnected_cells(instance.mesh_dir) == 5
    assert commands == ["checkMesh -allTopology -allGeometry"] * 2


def test_repeated_cleanup_without_progress_is_bounded(tmp_path, monkeypatch):
    instance = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model/p.cfdrun")
    instance._data = {"meshConfig": {"meshEngine": "dcCart"}}
    from backend.tests.test_air_nonempty_finite_preflight import _mesh
    _mesh(instance.mesh_dir, 1)
    monkeypatch.setattr(polymesh_io, "mesh_topology_summary", lambda *_: None)
    report = (
        "Writing region 0 (fully disconnected) with 10000 cells to cellSet region0\n"
        "Writing region 1 (fully disconnected) with 5 cells to cellSet region1\n"
        "Failed 1 mesh checks.\n"
    )
    calls = 0

    def check(*_):
        nonlocal calls
        calls += 1
        if calls > 8:
            raise AssertionError("cleanup repeated without a pass limit")
        return report

    monkeypatch.setattr(instance, "_run_cmd_capture", check)
    monkeypatch.setattr(instance, "_do_subset_mesh", Mock(return_value=5))
    with pytest.raises(RuntimeError, match="cleanup did not converge"):
        instance._remove_disconnected_cells(instance.mesh_dir)
    assert calls == 8


def test_cleanup_checks_the_cyclic_metadata_the_solve_will_read(tmp_path, monkeypatch):
    # The cyclic patches were normalised only at field generation, after this check: the boundary changed, the
    # quality receipt could not take the check's result and ran checkMesh again (Alibaba-hongchou: 92 s).
    instance = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model/p.cfdrun")
    instance._data = {"meshConfig": {"meshEngine": "dcCart"}}
    from backend.tests.test_air_nonempty_finite_preflight import _mesh
    _mesh(instance.mesh_dir, 1)
    boundary = instance.mesh_dir / "constant" / "polyMesh" / "boundary"
    text = boundary.read_text()
    head, _, tail = text.rpartition(")")
    boundary.write_text(head + "    pairA\n    {\n        type            cyclic;\n        nFaces          0;\n"
                        "        startFace       0;\n        matchTolerance  0.0001;\n        neighbourPatch  pairB;\n"
                        "    }\n)" + tail)
    monkeypatch.setattr(polymesh_io, "mesh_topology_summary", lambda *_: None)
    seen = []
    monkeypatch.setattr(instance, "_run_cmd_capture",
                        lambda cmd, _: (seen.append(boundary.read_text()), "Number of regions: 1 (OK).\nMesh OK.\n")[1])
    instance._remove_disconnected_cells(instance.mesh_dir)
    assert "matchTolerance  0.01;" in seen[0] or "matchTolerance 0.01;" in seen[0]
    assert "0.0001" not in seen[0]
    from backend.aircooling.runner.openfoam_case import fix_cyclic_match_tolerance
    before = boundary.read_bytes()
    fix_cyclic_match_tolerance(boundary)                      # field generation's call then changes nothing
    assert boundary.read_bytes() == before
