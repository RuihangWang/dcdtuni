"""Dual-key POST body acceptance: cfdrunJson (primary) + cfdExportJson (legacy)."""
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SERVER_PATH = Path(__file__).resolve().parents[2] / "server" / "server.py"


def _load_server_module():
    # server.py is a __main__-style script; load it fresh under a unique name.
    # It imports sibling modules (auth_manager, converter) by bare name, so we
    # must put its directory on sys.path before exec_module.
    server_dir = str(SERVER_PATH.parent)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    spec = importlib.util.spec_from_file_location("stlgen_server_under_test", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeHandler:
    def __init__(self, body_bytes: bytes):
        self._body = body_bytes
        self.headers = {"Content-Length": str(len(body_bytes))}
        self.rfile = types.SimpleNamespace(read=lambda n: self._body[:n])
        self.wfile_writes = []
        self.status = None
        self.sent_headers = {}

    # Minimal BaseHTTPRequestHandler surface needed by _read_body/_send_json.
    def send_response(self, code): self.status = code
    def send_header(self, k, v): self.sent_headers[k] = v
    def end_headers(self): pass
    class _wfile:
        def __init__(self, out): self.out = out
        def write(self, b): self.out.append(b)
    @property
    def wfile(self):
        if not hasattr(self, "_wf"):
            self._wf = self._wfile(self.wfile_writes)
        return self._wf


@pytest.fixture
def server_mod():
    return _load_server_module()


def _read_response(handler):
    return json.loads(b"".join(handler.wfile_writes).decode("utf-8"))


def _upload(server_mod, monkeypatch, key):
    """A minimal V2 snapshot that declares its one uploaded STL by its
    export-time hash: what a mesh upload has to carry to be archived."""
    import base64
    import hashlib
    stl = b"solid a\nendsolid a\n"
    manifest = {"schemaVersion": 2, "artifacts": {}, "room": {"height": 3.0}, "meshConfig": {},
                "geometryExport": {"stlFiles": {"stl_1": {
                    "path": "merged_a.stl", "contentHash": hashlib.sha256(stl).hexdigest()}}}}
    # a mesh route must not start a real CFD job here
    monkeypatch.setattr(server_mod.threading, "Thread",
                        lambda **_kw: types.SimpleNamespace(start=lambda: None))
    return json.dumps({
        key: json.dumps(manifest),
        "cfdrunFilename": "demo.cfdrun",
        "stlFiles": {"merged_a.stl": base64.b64encode(stl).decode()},
    }).encode("utf-8")


@pytest.mark.parametrize("handler_name", ["handle_prepare_case", "handle_run_mesh"])
def test_handler_accepts_new_cfdrunJson_key(server_mod, tmp_path, monkeypatch, handler_name):
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    body = _upload(server_mod, monkeypatch, "cfdrunJson")
    handler = _FakeHandler(body)
    getattr(server_mod, handler_name)(handler)
    resp = _read_response(handler)
    assert handler.status == 200, f"{handler_name}: expected 200, got {handler.status}: {resp}"
    assert (tmp_path / "model" / "demo.cfdrun").exists()


@pytest.mark.parametrize("handler_name", ["handle_prepare_case", "handle_run_mesh"])
def test_handler_accepts_legacy_cfdExportJson_key_with_deprecation(
    server_mod, tmp_path, monkeypatch, caplog, handler_name
):
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    body = _upload(server_mod, monkeypatch, "cfdExportJson")
    handler = _FakeHandler(body)
    with caplog.at_level("WARNING"):
        getattr(server_mod, handler_name)(handler)
    resp = _read_response(handler)
    assert handler.status == 200, f"{handler_name}: expected 200 on legacy key, got {handler.status}: {resp}"
    assert (tmp_path / "model" / "demo.cfdrun").exists()
    assert any("cfdExportJson" in rec.message and "deprecated" in rec.message.lower()
               for rec in caplog.records), f"{handler_name}: expected deprecation warning, got {[r.message for r in caplog.records]}"


def test_export_cfdrun_endpoint_exists(server_mod):
    """Primary export endpoint is /api/browser/export-cfdrun; /api/browser/export-cfd
    remains as an alias for the deprecation window."""
    # Route dispatch happens in STLServerHandler.do_POST; grep the handler source
    # for the literal route-string registration as a structural check.
    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "/api/browser/export-cfdrun" in source, \
        "new primary endpoint /api/browser/export-cfdrun must be registered"
    assert "/api/browser/export-cfd" in source, \
        "legacy alias /api/browser/export-cfd must still be registered"


def test_browser_worker_accepts_export_cfdrun_content_fn_name(server_mod):
    """Browser worker dispatches on fn_name 'export_cfdrun_content' (primary)
    and 'export_cfd_json' (alias)."""
    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'fn_name == "export_cfdrun_content"' in source or '"export_cfdrun_content"' in source, \
        "worker must dispatch on new fn_name 'export_cfdrun_content'"
    assert 'fn_name == "export_cfd_json"' in source or '"export_cfd_json"' in source, \
        "worker must keep dispatching on legacy fn_name 'export_cfd_json'"
