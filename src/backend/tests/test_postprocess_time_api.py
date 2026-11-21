"""The browser's selected OpenFOAM step must reach the native reader."""

from contextlib import nullcontext
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


SERVER_PATH = Path(__file__).resolve().parents[2] / "server/server.py"


@pytest.fixture
def server():
    server_dir = str(SERVER_PATH.parent)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    spec = importlib.util.spec_from_file_location("postprocess_time_server_under_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Handler:
    def __init__(self, body):
        payload = json.dumps(body).encode()
        self.headers = {"Content-Length": str(len(payload))}
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.status = None

    def send_response(self, code):
        self.status = code

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue())


class Processor:
    def __init__(self):
        self.calls = []

    def state_transition(self):
        return nullcontext()

    def init(self, run_dir, timeout=3600.0, time_value=None, case_version=None, case_dir=None):
        self.calls.append((run_dir, time_value))
        return {"success": True, "timeValue": time_value}


def test_postprocess_init_forwards_exact_selected_step(server, tmp_path, monkeypatch):
    run = tmp_path / "log" / "run"
    run.mkdir(parents=True)
    processor = Processor()
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    monkeypatch.setattr(server, "_get_cached_pp", lambda: processor)
    handler = Handler({"runDir": "log/run", "timeValue": 1})

    server.handle_pp_init(handler)

    assert handler.status == 200, handler.json()
    assert handler.json()["timeValue"] == 1
    assert processor.calls == [(run, 1)]


@pytest.mark.parametrize("bad", ["2", True, float("nan")])
def test_postprocess_init_rejects_invalid_step_before_reader(server, tmp_path, monkeypatch, bad):
    (tmp_path / "log/run").mkdir(parents=True)
    processor = Processor()
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    monkeypatch.setattr(server, "_get_cached_pp", lambda: processor)
    handler = Handler({"runDir": "log/run", "timeValue": bad})

    server.handle_pp_init(handler)

    assert handler.status == 400
    assert processor.calls == []
