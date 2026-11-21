"""A retained processor mesh must remain visible to both frontend clients."""

import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest

from backend.aircooling.runner.mesh_layout import (
    common_result_times,
    mesh_boundary_paths,
    mesh_exists,
    processor_mesh_dirs,
)


def _mesh(case, rank=None, patch="wall"):
    poly = (case if rank is None else case / f"processor{rank}") / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour"):
        (poly / name).write_text("FoamFile {}\n0\n(\n)\n")
    (poly / "boundary").write_text(
        f"1\n(\n    {patch}\n    {{\n        type wall;\n        nFaces 0;\n        startFace 0;\n    }}\n)\n"
    )
    return poly


def _time(case, rank, name, field="T"):
    directory = (case if rank is None else case / f"processor{rank}") / name
    directory.mkdir(parents=True, exist_ok=True)
    if field:
        (directory / field).write_text("field data")


def test_partition_discovery_rejects_missing_ranks_and_partial_meshes(tmp_path):
    first = _mesh(tmp_path, 0)
    third = _mesh(tmp_path, 2)
    assert not mesh_exists(tmp_path)
    second = _mesh(tmp_path, 1)
    assert processor_mesh_dirs(tmp_path) == [first, second, third]
    (second / "neighbour").unlink()
    assert not mesh_exists(tmp_path)


def test_serial_mesh_takes_precedence_over_old_processor_meshes(tmp_path):
    _mesh(tmp_path, 0)
    _mesh(tmp_path, 1)
    serial = _mesh(tmp_path)
    assert mesh_boundary_paths(tmp_path) == [serial / "boundary"]
    _time(tmp_path, None, "100")
    _time(tmp_path, 0, "200")
    _time(tmp_path, 1, "200")
    assert common_result_times(tmp_path) == [100.0]


def test_partition_discovery_rejects_missing_final_rank(tmp_path):
    _mesh(tmp_path, 0)
    system = tmp_path / "system"
    system.mkdir()
    (system / "decomposeParDict").write_text("// numberOfSubdomains 1;\nnumberOfSubdomains 2;\n")
    assert not mesh_exists(tmp_path)
    _mesh(tmp_path, 1)
    assert mesh_exists(tmp_path)


def test_result_times_require_all_ranks_and_preserve_numeric_aliases(tmp_path):
    for rank in range(2):
        _mesh(tmp_path, rank)
        _time(tmp_path, rank, "0")
        _time(tmp_path, rank, "100" if rank == 0 else "1e+02", "T.gz")
        _time(tmp_path, rank, "200", "T" if rank == 0 else None)
    _time(tmp_path, 0, "300")
    assert common_result_times(tmp_path) == [0.0, 100.0, 200.0]
    assert common_result_times(tmp_path, require_fields=True) == [0.0, 100.0]


@pytest.fixture
def server(monkeypatch, tmp_path):
    server_path = Path(__file__).resolve().parents[2] / "server" / "server.py"
    monkeypatch.syspath_prepend(str(server_path.parent))
    spec = importlib.util.spec_from_file_location("partition_status_server", server_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.CASE_DIR = tmp_path
    responses = []
    monkeypatch.setattr(module, "_send_json", lambda handler, code, data: responses.append((code, data)))
    module.test_responses = responses
    return module


def test_frontend_status_and_latest_run_discover_partitioned_results(server, tmp_path):
    run = tmp_path / "log" / "2026-09-10-10-00-00_demo"
    for rank in range(2):
        _mesh(tmp_path / "Mesh", rank)
        _mesh(run, rank)
        _time(run, rank, "0")
        _time(run, rank, "100")
    _time(run, 0, "200")

    server.handle_check_mesh(None)
    code, body = server.test_responses[-1]
    assert code == 200
    assert body["meshReady"] and body["decomposed"] and body["nPartitions"] == 2

    server.handle_scan_case_status(None)
    _, body = server.test_responses[-1]
    # Native partition existence remains observable, but this controlled
    # fixture has no strict quality receipt and cannot become solve-ready mesh.
    assert body["meshExists"] and not body["meshReady"]
    assert body["solveReady"] and body["postprocessReady"]
    assert body["logRuns"][0]["timesteps"] == [0, 100]
    assert body["logRuns"][0]["latestTimestep"] == 100
    assert server._find_latest_run_dir(tmp_path) == run
    assert server._find_latest_run_dir(tmp_path, "2025-") is None


def test_frontend_does_not_offer_partially_written_results(server, tmp_path):
    run = tmp_path / "log" / "2026-09-10-10-00-00_demo"
    for rank in range(2):
        _mesh(run, rank)
        _time(run, rank, "100", "T" if rank == 0 else None)
    server.handle_scan_case_status(None)
    _, body = server.test_responses[-1]
    assert not body["solveReady"]
    assert not body["postprocessReady"]


def test_old_root_mesh_does_not_hide_new_partition_results(server, tmp_path):
    run = tmp_path / "log" / "2026-09-11-10-00-00_demo"
    _mesh(run)
    _time(run, None, "0")
    for rank in range(2):
        _mesh(run, rank)
        _time(run, rank, "500")
    _time(run, 0, "600")
    server.handle_scan_case_status(None)
    _, body = server.test_responses[-1]
    assert body["postprocessReady"]
    assert body["logRuns"][0]["timesteps"] == [0, 500]
    assert body["logRuns"][0]["latestTimestep"] == 500


@pytest.mark.parametrize("decomposed", [False, True])
def test_explicit_checkmesh_uses_authoritative_mesh_layout(server, tmp_path, monkeypatch, decomposed):
    if decomposed:
        for rank in range(2):
            _mesh(tmp_path / "Mesh", rank)
    else:
        _mesh(tmp_path / "Mesh")
    for owner in (tmp_path / "Mesh").glob("**/polyMesh/owner"):
        owner.write_text('FoamFile { note "nCells:1"; }\\n0\\n(\\n)\\n')
    (tmp_path / "model").mkdir()
    (tmp_path / "model/test.cfdrun").write_text("{}")
    monkeypatch.setattr(server, "_read_body", lambda handler: {})
    monkeypatch.setattr(server.threading, "Timer",
        lambda *_a, **_kw: types.SimpleNamespace(start=lambda: None, cancel=lambda: None))
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return types.SimpleNamespace(
            stdout=io.StringIO("Mesh OK.\n"), returncode=0, wait=lambda: None,
        )

    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(
        server.threading, "Thread",
        lambda target, **kwargs: types.SimpleNamespace(start=target),
    )
    server.handle_run_check_mesh(None)
    code, body = server.test_responses[-1]
    assert code == 200
    expected = ["checkMesh", "-case", str(tmp_path / "Mesh"), "-constant", "-allTopology", "-allGeometry"]
    if decomposed:
        expected = ["mpirun", "-np", "2", *expected, "-parallel"]
    assert commands == [expected]
    assert server.JOBS[body["jobId"]]["checkMeshOk"] is True


def test_server_patch_list_merges_duplicate_partition_patch_names(server, tmp_path):
    _mesh(tmp_path / "Mesh", 0, "rack_inlet")
    _mesh(tmp_path / "Mesh", 1, "rack_inlet")
    _mesh(tmp_path / "Mesh", 2, "rack_outlet")
    server.handle_server_patches(None)
    code, body = server.test_responses[-1]
    assert code == 200
    assert [entry["patch"] for entry in body["patches"]] == ["rack_inlet", "rack_outlet"]


def test_mesh_preview_accepts_partition_only_case(server, tmp_path, monkeypatch):
    for rank in range(2):
        _mesh(tmp_path / "Mesh", rank)
    loader = types.SimpleNamespace(
        load=lambda: (b"", []),
        read_counts=lambda: {"nCells": 20, "nBoundaryFaces": 12, "nBoundarySum": 12},
    )
    cache = types.SimpleNamespace(get=lambda directory: loader)
    monkeypatch.setattr(server, "_get_mesh_preview_cache", lambda: cache)
    server.handle_mesh_preview_load(None)
    code, body = server.test_responses[-1]
    assert code == 200
    assert body["nCells"] == 20


def test_each_air_run_says_whether_it_is_transient(server, tmp_path):
    """The post-processing panel scrubs a PIMPLE run's times with the playhead (plan T5b)."""
    for name, application in (("2026-09-30-10-00-00_pimple", "buoyantBoussinesqPimpleFoam"),
                              ("2026-09-30-09-00-00_simple", "buoyantBoussinesqSimpleFoam_new"),
                              ("2026-09-30-08-00-00_nodict", None)):
        run = tmp_path / "log" / name
        _mesh(run)
        _time(run, None, "0")
        _time(run, None, "1")
        if application:
            (run / "system").mkdir()
            (run / "system" / "controlDict").write_text(f"application     {application};\nendTime 4;\n")
    server.handle_scan_case_status(None)
    _, body = server.test_responses[-1]
    assert {r["name"].split("_")[-1]: r["transient"] for r in body["logRuns"]} == {
        "pimple": True, "simple": False, "nodict": False}
