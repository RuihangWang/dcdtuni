"""Scene handoff: the server writes the editor scene for Kit under a signed
renderScene grant; Kit reads back exactly those bytes or refuses."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import zlib

import pytest

_SERVER_FILE = Path(__file__).resolve().parent.parent / "server.py"
_SPEC = importlib.util.spec_from_file_location("dcdtuni_server_handoff_under_test", _SERVER_FILE)
server = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = server
_SPEC.loader.exec_module(server)

KEY = "k" * 64
HASH = "a" * 64
SCENE_KEY = "b" * 64
PACKET = json.dumps({"schema": "dcdtuni.render-scene/v1", "primitives": []}).encode()


def handoff():
    return server._scene_handoff_module()


def test_written_scene_reads_back_with_its_grant(tmp_path):
    grant = handoff().write(str(tmp_path), PACKET, HASH, SCENE_KEY, KEY, now=1000.0)
    payload, raw = handoff().read(str(tmp_path), KEY, now=1001.0)
    assert raw == PACKET
    assert payload["packetSha256"] == hashlib.sha256(PACKET).hexdigest() == grant["payload"]["packetSha256"]
    assert (payload["projectHash"], payload["sceneKey"]) == (HASH, SCENE_KEY)
    grants = handoff()._grants()
    with pytest.raises(ValueError, match="kind"):  # never replayable as a dataset load
        grants.verify(grant, KEY, now=1001.0, roots=[handoff().directory(str(tmp_path))])


def test_changed_bytes_expired_grants_and_other_keys_are_refused(tmp_path):
    grant = handoff().write(str(tmp_path), PACKET, HASH, SCENE_KEY, KEY, now=1000.0)
    with pytest.raises(ValueError, match="expired"):
        handoff().read(str(tmp_path), KEY, now=1000.0 + handoff().GRANT_TTL_SECONDS + 1)
    with pytest.raises(ValueError, match="signature"):
        handoff().read(str(tmp_path), "z" * 64, now=1001.0)
    Path(grant["payload"]["path"]).write_bytes(PACKET.replace(b"[]", b"[1]"))
    with pytest.raises(ValueError, match="differs"):
        handoff().read(str(tmp_path), KEY, now=1001.0)


def test_a_new_scene_replaces_the_old_one_and_bad_identities_are_refused(tmp_path):
    first = handoff().write(str(tmp_path), PACKET, HASH, SCENE_KEY, KEY, now=1000.0)
    other = PACKET.replace(b"v1", b"v2")
    handoff().write(str(tmp_path), other, HASH, "c" * 64, KEY, now=1002.0)
    assert not os.path.exists(first["payload"]["path"])
    payload, raw = handoff().read(str(tmp_path), KEY, now=1003.0)
    assert raw == other and payload["sceneKey"] == "c" * 64
    for project_hash, scene_key in (("A" * 64, SCENE_KEY), (HASH, "short"), (HASH, "../" * 20)):
        with pytest.raises(ValueError):
            handoff().write(str(tmp_path), PACKET, project_hash, scene_key, KEY)
    with pytest.raises(ValueError):
        handoff().write(str(tmp_path), b"", HASH, SCENE_KEY, KEY)


class _Handler:
    def __init__(self, body: bytes, headers: dict):
        self.rfile = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body)), **headers}


def _post(monkeypatch, body: bytes, headers: dict):
    sent = []
    monkeypatch.setattr(server, "_send_json", lambda handler, status, data: sent.append((status, data)))
    server.handle_ov_scene_handoff(_Handler(body, headers))
    return sent[0]


def test_endpoint_inflates_and_hands_the_scene_to_kit(monkeypatch, tmp_path):
    monkeypatch.setenv("DCDTUNI_OV_GRANT_KEY", KEY)
    monkeypatch.setenv("DCDTUNI_OV_RUNTIME_DIR", str(tmp_path))
    status, data = _post(monkeypatch, zlib.compress(PACKET), {"X-Project-Hash": HASH, "X-Scene-Key": SCENE_KEY})
    assert status == 200 and data["bytes"] == len(PACKET)
    payload, raw = handoff().read(str(tmp_path), KEY)
    assert raw == PACKET and payload["sceneKey"] == SCENE_KEY
    status, data = _post(monkeypatch, b"not deflate", {"X-Project-Hash": HASH, "X-Scene-Key": SCENE_KEY})
    assert status == 400
    status, data = _post(monkeypatch, zlib.compress(b"[1, 2]"), {"X-Project-Hash": HASH, "X-Scene-Key": SCENE_KEY})
    assert status == 400


def test_endpoint_without_a_kit_runtime_is_503(monkeypatch):
    monkeypatch.setenv("DCDTUNI_OV_GRANT_KEY", KEY)
    monkeypatch.delenv("DCDTUNI_OV_RUNTIME_DIR", raising=False)
    status, data = _post(monkeypatch, zlib.compress(PACKET), {"X-Project-Hash": HASH, "X-Scene-Key": SCENE_KEY})
    assert status == 503 and data["code"] == "ov_handoff_unavailable"
