"""Case upload must not replace inputs while a mesh/solve process owns them."""

import base64
import importlib.util
import json
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest


SERVER_PATH = Path(__file__).resolve().parents[2] / "server" / "server.py"


class _Handler:
    def __init__(self, payload):
        body = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = types.SimpleNamespace(read=lambda size: body[:size])
        self.status = None
        self.writes = []
        self.wfile = types.SimpleNamespace(write=self.writes.append)

    def send_response(self, status):
        self.status = status

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass

    @property
    def response(self):
        return json.loads(b"".join(self.writes))


@pytest.fixture
def server_mod(monkeypatch):
    server_dir = str(SERVER_PATH.parent)
    monkeypatch.syspath_prepend(server_dir)
    spec = importlib.util.spec_from_file_location("case_upload_server_under_test", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DCTWIN_AVAILABLE", True)
    # A legacy run-mesh route must not start a real CFD subprocess in this test.
    monkeypatch.setattr(mod.threading, "Thread", lambda **_kw: types.SimpleNamespace(start=lambda: None))
    return mod


def _payload(stl_files=None):
    return {
        "cfdrunJson": '{"schemaVersion": 1, "revision": "replacement"}',
        "cfdrunFilename": "next.cfdrun",
        "stlFiles": stl_files if stl_files is not None else {"merged_new.stl": base64.b64encode(b"new stl").decode()},
        "meshOnly": True,
    }


def _old_inputs(case_dir):
    model = case_dir / "model"
    tri = case_dir / "Mesh" / "constant" / "triSurface"
    model.mkdir(parents=True)
    tri.mkdir(parents=True)
    (model / "old.cfdrun").write_bytes(b"old manifest")
    (tri / "merged_old.stl").write_bytes(b"old stl")
    return model, tri


@contextmanager
def _another_process_holds_lock(case_dir):
    lock_path = case_dir / ".dcdtuni-case.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read(1)", str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        yield
    finally:
        child.stdin.write("x")
        child.stdin.flush()
        child.wait(timeout=5)


@pytest.mark.parametrize("route", ["handle_prepare_case", "handle_run_mesh"])
def test_another_process_lock_rejects_upload_without_touching_inputs(server_mod, tmp_path, monkeypatch, route):
    """Removing the file-lock guard would overwrite an active runner's exact input."""
    model, tri = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    with _another_process_holds_lock(tmp_path):
        handler = _Handler(_payload())
        getattr(server_mod, route)(handler)
        assert handler.status == 409, handler.response
        assert (model / "old.cfdrun").read_bytes() == b"old manifest"
        assert not (model / "next.cfdrun").exists()
        assert (tri / "merged_old.stl").read_bytes() == b"old stl"
        assert not (tri / "merged_new.stl").exists()


@pytest.mark.parametrize("route", ["handle_browser_export_stl", "handle_browser_export_case", "handle_browser_full_pipeline"])
def test_browser_export_writers_cannot_bypass_case_lock(server_mod, tmp_path, monkeypatch, route):
    """Leaving any headless-browser writer unlocked permits a live CFD input rewrite."""
    model, tri = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    monkeypatch.setattr(server_mod, "_ensure_browser", lambda _handler: True)
    worker = server_mod._BrowserWorkerState()
    worker.ready.set()
    monkeypatch.setattr(server_mod, "_browser_state", worker)
    encoded = base64.b64encode(b"new stl").decode()

    def browser_call(_kind, script):
        if _kind == "load_project":
            return {"success": True, "modelCount": 1}
        if "buildGroupedStlMap" in script:
            if route == "handle_browser_export_stl":
                return {"stlFiles": {"merged_new.stl": encoded}, "count": 1}
            return {"merged_new.stl": encoded}
        if "buildCfdrunJsonV2" in script:
            return '{"schemaVersion": 1, "revision": "replacement"}'
        if "getCfdrunFilename" in script:
            return "next.cfdrun"
        return 1

    monkeypatch.setattr(server_mod, "_browser_call", browser_call)
    with _another_process_holds_lock(tmp_path):
        handler = _Handler({"projPath": "mock.cfdproj"})
        getattr(server_mod, route)(handler)
        assert handler.status == 409, handler.response
        assert (model / "old.cfdrun").read_bytes() == b"old manifest"
        assert not (model / "next.cfdrun").exists()
        assert (tri / "merged_old.stl").read_bytes() == b"old stl"
        assert not (tri / "merged_new.stl").exists()


@pytest.mark.parametrize("relative_path", [
    "model/old.cfdrun", "Mesh/constant/triSurface/merged_old.stl",
    "Mesh/constant/polyMesh/faces", "Mesh/processor0/constant/polyMesh/faces",
    "Mesh/0/T", "Mesh/system/fvOptions", "heat-ledger.json",
    "mesh-quality-receipt.json", "source-surface-mesh-source.json",
    "log/run/1/T",
])
def test_write_file_cannot_bypass_active_case_lock(server_mod, tmp_path, relative_path):
    """Admin write-file has to respect the same lock for protected CFD inputs."""
    _old_inputs(tmp_path)
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"original")
    before = path.read_bytes()
    with _another_process_holds_lock(tmp_path):
        handler = _Handler({"path": str(path), "content": "replacement", "overwrite": True})
        server_mod.handle_write_file(handler)
        assert handler.status == 409, handler.response
        assert path.read_bytes() == before


@pytest.mark.parametrize("route", ["handle_prepare_case", "handle_run_mesh"])
def test_invalid_second_stl_fails_entire_upload(server_mod, tmp_path, monkeypatch, route):
    """A bad later member cannot leave a new manifest plus a partially replaced STL set."""
    model, tri = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    first = base64.b64encode(b"first stl").decode()
    handler = _Handler(_payload({"merged_first.stl": first, "merged_second.stl": "!!!invalid!!!"}))
    getattr(server_mod, route)(handler)
    assert handler.status == 400, handler.response
    assert (model / "old.cfdrun").read_bytes() == b"old manifest"
    assert not (model / "next.cfdrun").exists()
    assert (tri / "merged_old.stl").read_bytes() == b"old stl"
    assert not (tri / "merged_first.stl").exists()
    assert not (tri / "merged_second.stl").exists()


@pytest.mark.parametrize("route", ["handle_prepare_case", "handle_run_mesh"])
def test_staging_io_error_fails_entire_upload(server_mod, tmp_path, monkeypatch, route):
    """Disk failure while staging a later STL cannot delete the previous batch."""
    model, tri = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    original_write = Path.write_bytes

    def fail_second(self, content):
        if self.name == "merged_second.stl":
            raise OSError("synthetic disk error")
        return original_write(self, content)

    monkeypatch.setattr(Path, "write_bytes", fail_second)
    encoded = base64.b64encode(b"new stl").decode()
    payload = _payload({"merged_first.stl": encoded, "merged_second.stl": encoded})
    if route == "handle_run_mesh":
        payload["cfdrunJson"] = '{"schemaVersion": 2, "artifacts": {}}'
    handler = _Handler(payload)
    getattr(server_mod, route)(handler)
    assert handler.status == 500, handler.response
    assert (model / "old.cfdrun").read_bytes() == b"old manifest"
    assert not (model / "next.cfdrun").exists()
    assert (tri / "merged_old.stl").read_bytes() == b"old stl"
    assert not (tri / "merged_first.stl").exists()


def test_prepare_command_pins_materialized_manifest_sha(server_mod, tmp_path, monkeypatch):
    """Without a SHA argument a later upload can replace bytes before terminal execution."""
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    handler = _Handler(_payload({}))
    server_mod.handle_prepare_case(handler)
    assert handler.status == 200, handler.response
    command = handler.response["command"]
    cfdrun = tmp_path / "model" / "next.cfdrun"
    import hashlib
    expected = hashlib.sha256(cfdrun.read_bytes()).hexdigest()
    assert f"--expected-cfdrun-sha256 {expected}" in command


def test_successful_batch_publishes_v2_paths_and_exact_sha(server_mod, tmp_path, monkeypatch):
    """A staged manifest must name the final case paths, with SHA of those bytes."""
    model, tri = _old_inputs(tmp_path)
    (model / "keep.txt").write_text("model note")
    (tri / "keep.txt").write_text("surface note")
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    payload = _payload()
    payload["cfdrunJson"] = '{"schemaVersion": 2, "artifacts": {}}'
    handler = _Handler(payload)
    server_mod.handle_prepare_case(handler)
    assert handler.status == 200, handler.response
    assert sorted(p.name for p in model.glob("*.cfdrun")) == ["next.cfdrun"]
    assert (model / "keep.txt").read_text() == "model note"
    assert (tri / "keep.txt").read_text() == "surface note"
    assert sorted(p.name for p in tri.glob("*.stl")) == ["merged_new.stl"]
    manifest_bytes = (model / "next.cfdrun").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["artifacts"]["caseDir"] == str(tmp_path.resolve())
    assert manifest["artifacts"]["modelDir"] == str(model.resolve())
    assert manifest["artifacts"]["triSurfaceDir"] == str(tri.resolve())
    import hashlib
    assert handler.response["cfdrunSha256"] == hashlib.sha256(manifest_bytes).hexdigest()


def test_commit_rename_error_restores_old_manifest_and_stls(server_mod, tmp_path, monkeypatch):
    """Failure after a manifest swap must roll back it and the untouched STL set."""
    model, tri = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    original_rename = Path.rename

    def fail_staged_tri(self, target):
        if self.name == "triSurface" and self.parent.name == "stage":
            raise OSError("synthetic rename error")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_staged_tri)
    handler = _Handler(_payload())
    server_mod.handle_prepare_case(handler)
    assert handler.status == 500, handler.response
    assert (model / "old.cfdrun").read_bytes() == b"old manifest"
    assert not (model / "next.cfdrun").exists()
    assert (tri / "merged_old.stl").read_bytes() == b"old stl"
    assert not (tri / "merged_new.stl").exists()


def test_write_file_allows_project_save_while_case_runs(server_mod, tmp_path):
    """Editing the project remains separate from published runtime inputs."""
    _old_inputs(tmp_path)
    project = tmp_path / "model" / "draft.cfdproj"
    with _another_process_holds_lock(tmp_path):
        handler = _Handler({"path": str(project), "content": "draft", "overwrite": True})
        server_mod.handle_write_file(handler)
        assert handler.status == 200, handler.response
        assert project.read_text() == "draft"


@pytest.mark.parametrize("kind", ["manifest", "stl"])
def test_staged_upload_does_not_follow_existing_input_symlink(server_mod, tmp_path, monkeypatch, kind):
    """A stale symlink cannot redirect new CFD input bytes outside the case."""
    model, tri = _old_inputs(tmp_path)
    outside = tmp_path / "outside.txt"
    linked = (model / "next.cfdrun" if kind == "manifest" else tri / "merged_new.stl")
    linked.symlink_to(outside)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    handler = _Handler(_payload())
    server_mod.handle_prepare_case(handler)
    assert handler.status == 200, handler.response
    assert not outside.exists()
    assert not linked.is_symlink()
    assert linked.read_bytes() == (b"new stl" if kind == "stl" else _payload()["cfdrunJson"].encode())


def test_active_manifest_sweep_removes_alias_symlink(server_mod, tmp_path, monkeypatch):
    """A symlink alias to the new path still counts as a second active manifest."""
    model, _tri = _old_inputs(tmp_path)
    (model / "old.cfdrun").unlink()
    (model / "next.cfdrun").write_text("prior")
    (model / "old.cfdrun").symlink_to("next.cfdrun")
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    handler = _Handler(_payload({}))
    server_mod.handle_prepare_case(handler)
    assert handler.status == 200, handler.response
    assert [p.name for p in model.glob("*.cfdrun")] == ["next.cfdrun"]


@pytest.mark.parametrize("route", ["handle_browser_export_stl", "handle_browser_export_case", "handle_browser_full_pipeline"])
def test_empty_browser_stl_export_removes_previous_surfaces(server_mod, tmp_path, monkeypatch, route):
    """Deleting all geometry in the browser must not leave last run's STLs."""
    _model, tri = _old_inputs(tmp_path)
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    monkeypatch.setattr(server_mod, "_ensure_browser", lambda _handler: True)
    worker = server_mod._BrowserWorkerState()
    worker.ready.set()
    monkeypatch.setattr(server_mod, "_browser_state", worker)

    def browser_call(kind, script):
        if kind == "load_project":
            return {"success": True, "modelCount": 0}
        if "buildGroupedStlMap" in script:
            return {"stlFiles": {}, "count": 0} if route == "handle_browser_export_stl" else {}
        if "buildCfdrunJsonV2" in script:
            # The supervised pipeline consumes supported immutable v2 snapshots.
            return '{"schemaVersion": 2, "artifacts": {}}' if route == "handle_browser_full_pipeline" else '{"schemaVersion": 1, "revision": "empty"}'
        if "getCfdrunFilename" in script:
            return "empty.cfdrun"
        return 0

    monkeypatch.setattr(server_mod, "_browser_call", browser_call)
    handler = _Handler({"projPath": "empty.cfdproj"})
    getattr(server_mod, route)(handler)
    assert handler.status == 200, handler.response
    assert list(tri.glob("*.stl")) == []
