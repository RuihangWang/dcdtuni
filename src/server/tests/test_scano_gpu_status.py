"""/api/ai/scano/gpu (what the AI solve page shows live) and the inference keys of an AI request."""
from __future__ import annotations

import types

import pytest

import scano_ai
import server
from backend.tests.test_scano_ai import reconstructed_case


def test_inference_keys_of_a_request():
    assert server._scano_inference({}) == {"inference": "auto", "passes": 1}
    assert server._scano_inference({"inference": "tta", "passes": "4"}) == {"inference": "tta", "passes": 4}
    for bad in ({"inference": "fast"}, {"passes": 0}, {"passes": 17}, {"passes": "x"}):
        with pytest.raises(ValueError):
            server._scano_inference(bad)


def test_gpu_status_reports_the_card_the_room_and_the_plan(tmp_path, monkeypatch):
    case = tmp_path / "case"
    reconstructed_case(case / "Mesh")
    replies = []
    monkeypatch.setattr(server, "_send_json", lambda _h, code, data: replies.append((code, data)))
    monkeypatch.setattr(server, "CASE_DIR", case)
    monkeypatch.setattr(server, "_scano_gpu_target", lambda: {"index": "5", "uuid": "GPU-x", "name": "RTX"})
    reading = {"index": "5", "uuid": "GPU-x", "name": "RTX", "totalMiB": 24564, "usedMiB": 564, "freeMiB": 24000,
               "utilization": 0, "workerMiB": None}
    monkeypatch.setattr(scano_ai, "gpu_reading", lambda uuid, pid: reading if uuid == "GPU-x" else None)
    handler = types.SimpleNamespace(path="/api/ai/scano/gpu?model=scano_c118_s18&inference=auto&passes=2")
    server.handle_ai_scano_gpu(handler)
    code, body = replies[-1]
    assert code == 200 and body["gpu"]["index"] == "5" and body["freeForAiBytes"] == 24000 * 2**20
    assert body["room"]["nodes"] == 12 and body["running"] is False
    plan = body["plan"]
    assert plan["mode"] == "whole" and plan["fits"] and plan["forwards"] == 1 and plan["passes"] is None
    handler.path = "/api/ai/scano/gpu?model=scano_c118_s18&inference=tta&passes=2"
    server.handle_ai_scano_gpu(handler)
    plan = replies[-1][1]["plan"]
    assert plan["mode"] == "tta" and plan["passes"] == 2 and plan["forwards"] == 2 and plan["ttaBytes"] > 0
