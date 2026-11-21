"""Exercise the real runner's diagnostics with two actual CLI report bodies.

Native execution is replaced at _run_cmd; these tests do not generate a mesh.
The JSON fixtures are byte copies from normal React job b5a0cbc5 (2026-10-06).
"""
from pathlib import Path
import shlex

import pytest

from backend.aircooling.runner import stlgen_runner
from backend.aircooling.runner.stlgen_runner import MeshTimeoutError, STLGenRunner

REPORTS = Path(__file__).parent / "fixtures/meshgen_reports"
INGEST = (REPORTS / "ingest-mapping.json").read_bytes()
NATIVE_S7 = (REPORTS / "native-s7.json").read_bytes()


class ControlledMeshgen(STLGenRunner):
    """The production diagnostic/assembly methods run; no native command does."""

    def __init__(self, case):
        super().__init__(case, cfdrun_path=case / "model/input.cfdrun")
        self.commands = []

    def _start_mesh_budget(self):
        pass

    def _ensure_mesh_system_files(self):
        pass

    def _resolve_meshgen_binary(self):
        return "/controlled/meshgen-cli"

    def _meshgen_thread_prefix(self):
        return ""

    def _warn_open_room_enclosure(self, case):
        pytest.fail("a refused export reached the enclosure step")

    def _remove_disconnected_cells(self, case, *, snap_off=None):
        pytest.fail("a refused export reached quality/cleanup")


def make_runner(tmp_path, monkeypatch):
    runner = ControlledMeshgen(tmp_path)
    runner.tri_surface_dir.mkdir(parents=True, exist_ok=True)
    (runner.tri_surface_dir / "room.stl").write_text("solid room\nendsolid room\n")
    errors = []
    monkeypatch.setattr(stlgen_runner.logger, "error", lambda message: errors.append(str(message)))
    return runner, errors


def write_reports(scratch, *, native=True):
    (scratch / "dc_report.json").write_bytes(INGEST)
    if native:
        (scratch / "constant/dc_report.json").write_bytes(NATIVE_S7)


def assert_s7_diagnostic(errors):
    assert any("stage=S7 mode=dcMode-report-not-refusal" in line for line in errors)
    assert any("nonManifoldShells=1 problemShells=1" in line for line in errors)
    assert not any("stage=None" in line for line in errors)
    assert not any("dc17-ingest-mapping/v1" in line for line in errors)


@pytest.mark.parametrize("failure", ["rc1", "timeout", "missing_export"])
def test_native_failure_timeout_and_missing_export_read_s7_not_ingest(tmp_path, monkeypatch, failure):
    runner, errors = make_runner(tmp_path, monkeypatch)
    scratch = runner.mesh_dir / ".meshgen_scratch"
    native_error = MeshTimeoutError("controlled deadline") if failure == "timeout" else RuntimeError("controlled rc1")

    def command(cmd, workdir):
        runner.commands.append((shlex.split(cmd), workdir))
        write_reports(workdir)
        if failure != "missing_export":
            raise native_error

    monkeypatch.setattr(runner, "_run_cmd", command)
    with pytest.raises(MeshTimeoutError if failure == "timeout" else RuntimeError) as caught:
        runner._run_meshgen_pipeline()
    if failure == "timeout":
        assert caught.value is native_error
    elif failure == "rc1":
        assert caught.value.__cause__ is native_error
        assert str(scratch / "constant/dc_report.json") in str(caught.value)
    else:
        assert "no usable polyMesh" in str(caught.value)
    words, workdir = runner.commands[0]
    assert workdir == scratch
    assert words[words.index("--report") + 1] == str(scratch / "dc_report.json")
    assert words[words.index("--export-foam") + 1] == str(scratch / "constant/polyMesh")
    assert words[words.index("--dc-mode"):words.index("--export-foam")] == ["--dc-mode", "--dc-raw", "--dc-seeds", "pre"]
    assert_s7_diagnostic(errors)
    assert (scratch / "dc_report.json").read_bytes() == INGEST
    assert (scratch / "constant/dc_report.json").read_bytes() == NATIVE_S7
    assert not (runner.mesh_dir / "constant/polyMesh").exists()


def test_missing_native_report_never_falls_back_to_valid_ingest(tmp_path, monkeypatch):
    runner, errors = make_runner(tmp_path, monkeypatch)
    scratch = runner.mesh_dir / ".meshgen_scratch"

    def command(cmd, workdir):
        write_reports(workdir, native=False)
        raise RuntimeError("controlled rc1 before native report")

    monkeypatch.setattr(runner, "_run_cmd", command)
    with pytest.raises(RuntimeError, match="refused the mesh"):
        runner._run_meshgen_pipeline()
    assert any("no native S7 report" in line and str(scratch / "constant/dc_report.json") in line for line in errors)
    assert not any("stage=None" in line or "full report:" in line for line in errors)
    assert (scratch / "dc_report.json").read_bytes() == INGEST


def test_ingest_body_is_explicitly_rejected_as_native_s7(tmp_path, monkeypatch):
    runner, errors = make_runner(tmp_path, monkeypatch)
    wrong = tmp_path / "dc_report.json"
    wrong.write_bytes(INGEST)
    runner._log_meshgen_report(wrong)
    assert any("not a native S7 report" in line and "dc17-ingest-mapping/v1" in line for line in errors)
    # A rejection may quote the absent stage; it must not emit a verdict summary.
    assert not any(line.startswith("dc_report.json: stage=") or "full report:" in line for line in errors)


def test_actual_native_s7_body_remains_a_diagnostic_not_mesh_acceptance(tmp_path, monkeypatch):
    runner, errors = make_runner(tmp_path, monkeypatch)
    report = tmp_path / "dc_report.json"
    report.write_bytes(NATIVE_S7)
    assert runner._log_meshgen_report(report) is None
    assert_s7_diagnostic(errors)
    assert not (runner.mesh_dir / "constant/polyMesh").exists()


def test_degenerate_floor_diagnostic_uses_export_sibling_s7(tmp_path, monkeypatch):
    runner, errors = make_runner(tmp_path, monkeypatch)
    scratch = runner.mesh_dir / ".meshgen_scratch"
    out_poly = scratch / "constant/polyMesh"
    out_poly.mkdir(parents=True)
    write_reports(scratch)
    for name in runner._MESHGEN_POLYMESH_REQUIRED:
        (out_poly / name).write_text("controlled export\n")
    from backend.aircooling.runner import polymesh_io
    monkeypatch.setattr(polymesh_io, "mesh_topology_summary", lambda path: {"nCells": 2})
    with pytest.raises(RuntimeError, match="2 cells < 1000 floor"):
        runner._assemble_meshgen_polymesh(runner.mesh_dir, out_poly, scratch)
    assert_s7_diagnostic(errors)
    assert not (runner.mesh_dir / "constant/polyMesh").exists()
