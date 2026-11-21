"""A browser request binds its own case even when another tab selects a case."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
import pytest
from backend.tests.test_case_upload_lock import server_mod, _Handler, _payload as _legacy_payload, _old_inputs

def _payload():
    # A mesh upload is archived as the original input batch, which needs the
    # manifest to declare each uploaded STL by name and export-time hash.
    stl = {"stl_1": {"path": "merged_new.stl", "contentHash": hashlib.sha256(b"new stl").hexdigest()}}
    manifest = {"schemaVersion": 2, "artifacts": {}, "geometryExport": {"stlFiles": stl}}
    return {**_legacy_payload(), "cfdrunJson": json.dumps(manifest)}

@pytest.mark.parametrize("route", ["handle_run_mesh", "handle_prepare_case"])
def test_explicit_case_upload_does_not_write_other_tabs_case(server_mod, tmp_path, monkeypatch, route):
    own, other = tmp_path / "own", tmp_path / "other"
    model, tri = _old_inputs(own)
    other_model, other_tri = _old_inputs(other)
    monkeypatch.setattr(server_mod, "CASE_DIR", other)
    handler = _Handler({**_payload(), "caseDir": str(own)})
    getattr(server_mod, route)(handler)
    assert handler.status == 200, handler.response
    assert (model / "next.cfdrun").is_file()
    assert (tri / "merged_new.stl").read_bytes() == b"new stl"
    assert (other_model / "old.cfdrun").read_bytes() == b"old manifest"
    assert (other_tri / "merged_old.stl").read_bytes() == b"old stl"
    assert not (other_model / "next.cfdrun").exists()
    assert server_mod.CASE_DIR == other
    assert handler.response["caseDir"] == str(own.resolve())

@pytest.mark.parametrize("route", ["handle_run_mesh", "handle_prepare_case", "handle_run_check_mesh"])
@pytest.mark.parametrize("bad", [None, True, "", "relative", "missing", "file"])
def test_explicit_invalid_case_never_falls_back(server_mod, tmp_path, monkeypatch, route, bad):
    other = tmp_path / "other"
    model, tri = _old_inputs(other)
    monkeypatch.setattr(server_mod, "CASE_DIR", other)
    if bad == "missing": bad = str(tmp_path / "absent")
    if bad == "file":
        f = tmp_path / "file.txt"; f.write_text("held"); bad = str(f)
    handler = _Handler({**_payload(), "caseDir": bad})
    getattr(server_mod, route)(handler)
    assert handler.status == 400, handler.response
    assert (model / "old.cfdrun").read_bytes() == b"old manifest"
    assert not (model / "next.cfdrun").exists()
    assert (tri / "merged_old.stl").read_bytes() == b"old stl"
    assert not (tmp_path / "absent").exists()

@pytest.mark.parametrize("route", ["handle_check_mesh", "handle_scan_case_status"])
def test_readiness_is_read_from_request_case(server_mod, tmp_path, monkeypatch, route):
    own, other = tmp_path / "own", tmp_path / "other"
    own.mkdir(); other.mkdir()
    monkeypatch.setattr(server_mod, "CASE_DIR", other)
    if route == "handle_check_mesh":
        import backend.aircooling.runner.mesh_layout as layout
        monkeypatch.setattr(layout, "mesh_directories", lambda path: [path / "constant" / "polyMesh"] if path.parent == own else [])
    else:
        monkeypatch.setattr(server_mod, "_scan_case_mesh_status", lambda path: {"meshExists": path == own, "meshReady": path == own})
    handler = _Handler({}); handler.path = '/api/status?caseDir=' + quote(str(own), safe='')
    getattr(server_mod, route)(handler)
    assert handler.status == 200, handler.response
    assert handler.response["meshReady"] is True
    assert server_mod.CASE_DIR == other

@pytest.mark.parametrize("query", ["caseDir=", "caseDir=relative", "caseDir=%2Fmissing-request-case", "caseDir=%2Fone&caseDir=%2Ftwo"])
@pytest.mark.parametrize("route", ["handle_check_mesh", "handle_scan_case_status"])
def test_invalid_readiness_query_is_rejected(server_mod, tmp_path, monkeypatch, route, query):
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    handler = _Handler({}); handler.path = '/api/status?' + query
    getattr(server_mod, route)(handler)
    assert handler.status == 400, handler.response


def test_legacy_upload_without_case_dir_still_uses_server_case(server_mod, tmp_path, monkeypatch):
    model, _ = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    handler = _Handler(_payload())
    server_mod.handle_run_mesh(handler)
    assert handler.status == 200
    assert (model / "next.cfdrun").exists()


def test_worker_keeps_request_case_after_later_global_switch(server_mod, tmp_path, monkeypatch):
    own, other = tmp_path / "own", tmp_path / "other"
    _old_inputs(own); _old_inputs(other)
    monkeypatch.setattr(server_mod, "CASE_DIR", other)
    queued = []
    monkeypatch.setattr(server_mod, "_start_job_worker", queued.append)
    handler = _Handler({**_payload(), "caseDir": str(own)})
    server_mod.handle_run_mesh(handler)
    assert handler.status == 200
    changed = tmp_path / "third"; changed.mkdir()
    monkeypatch.setattr(server_mod, "CASE_DIR", changed)
    monkeypatch.setattr(server_mod, "_release_idle_software_browser", lambda: {"status": "test"})
    captured = []
    def stop_before_spawn(job_id, cmd, **kw):
        captured.append((cmd, kw["cwd"]))
        raise RuntimeError("test stops before subprocess execution")
    monkeypatch.setattr(server_mod, "_spawn_job_process", stop_before_spawn)
    queued[0]()
    assert captured[0][1] == str(own.resolve())
    assert str(own / "model" / "next.cfdrun") in captured[0][0]
