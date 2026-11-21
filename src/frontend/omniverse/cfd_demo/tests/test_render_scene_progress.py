"""CPU-only render-scene progress barrier tests; Kit itself is never started."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

EXTENSION_ROOT = Path(__file__).resolve().parents[1] / "kit/extensions/dc.cfd_bridge"
sys.path.insert(0, str(EXTENSION_ROOT))
from dc.cfd_bridge.render_scene import SceneTransfer, validate_overlay  # noqa: E402


def _extension_without_kit(monkeypatch):
    """Load the real route method with only its Kit imports stubbed."""
    omni = types.ModuleType("omni")
    omni.__path__ = []
    omni_ext = types.ModuleType("omni.ext")
    omni_ext.IExt = type("IExt", (), {})
    omni_usd = types.ModuleType("omni.usd")
    omni_usd.get_context = lambda: types.SimpleNamespace(get_stage=lambda: None)
    omni_kit = types.ModuleType("omni.kit")
    omni_kit.__path__ = []
    omni_kit_app = types.ModuleType("omni.kit.app")
    omni.ext, omni.usd, omni.kit = omni_ext, omni_usd, omni_kit
    omni_kit.app = omni_kit_app
    carb = types.ModuleType("carb")
    carb.__path__ = []
    carb_events = types.ModuleType("carb.events")
    carb_eventdispatcher = types.ModuleType("carb.eventdispatcher")
    carb.events, carb.eventdispatcher = carb_events, carb_eventdispatcher
    for name, module in {
        "omni": omni, "omni.ext": omni_ext, "omni.usd": omni_usd,
        "omni.kit": omni_kit, "omni.kit.app": omni_kit_app,
        "carb": carb, "carb.events": carb_events,
        "carb.eventdispatcher": carb_eventdispatcher,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = EXTENSION_ROOT / "dc/cfd_bridge/extension.py"
    spec = importlib.util.spec_from_file_location("dc.cfd_bridge._progress_test_extension", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    extension = module.CfdBridgeExtension()
    extension._render_transfer = SceneTransfer()
    acks = []
    extension._render_ack = lambda request_id, **result: acks.append((request_id, result))
    return extension, acks


def _progress_command(extension, request_id, scene_id, received):
    extension._route_command({
        "command_name": "renderSceneProgress",
        "message": json.dumps({
            "requestId": request_id,
            "sceneRequestId": scene_id,
            "received": received,
        }),
    })


def test_transfer_progress_counts_only_ordered_chunks_and_rejects_wrong_or_expired_id():
    transfer = SceneTransfer()
    with pytest.raises(ValueError, match="Missing or expired"):
        transfer.progress("scene-a")
    transfer.begin("scene-a", 3)
    assert transfer.progress("scene-a") == 0
    transfer.chunk("scene-a", 0, "first")
    transfer.chunk("scene-a", 1, "second")
    stamp = transfer.current["at"]
    assert transfer.progress("scene-a") == 2
    assert transfer.current["at"] == stamp  # Polling does not extend the 120 s deadline.
    with pytest.raises(ValueError, match="Missing or expired"):
        transfer.progress("scene-b")
    transfer.current["at"] -= 121
    with pytest.raises(ValueError, match="Missing or expired"):
        transfer.progress("scene-a")


def test_progress_command_ack_is_a_count_barrier_with_distinct_request_ids(monkeypatch):
    extension, acks = _extension_without_kit(monkeypatch)
    extension._render_transfer.begin("scene-a", 2)
    extension._render_transfer.chunk("scene-a", 0, "SENSITIVE_SCENE_CHUNK")
    _progress_command(extension, "barrier-early", "scene-a", 2)
    assert acks == [("barrier-early", {"ok": False, "error": "Scene progress mismatch: expected 2, actual 1"})]
    _progress_command(extension, "barrier-ready", "scene-a", 1)
    assert acks[-1] == ("barrier-ready", {"ok": True, "received": 1})
    assert all("SENSITIVE_SCENE_CHUNK" not in str(ack) for ack in acks)


def test_progress_command_reports_invalid_id_count_and_expiry_without_scene_data(monkeypatch):
    extension, acks = _extension_without_kit(monkeypatch)
    extension._render_transfer.begin("scene-a", 2)
    extension._render_transfer.chunk("scene-a", 0, "SENSITIVE_SCENE_CHUNK")
    _progress_command(extension, "wrong-scene", "scene-b", 1)
    _progress_command(extension, "bad-count", "scene-a", True)
    extension._render_transfer.current["at"] -= 121
    _progress_command(extension, "expired", "scene-a", 1)
    assert [item[0] for item in acks] == ["wrong-scene", "bad-count", "expired"]
    assert all(item[1]["ok"] is False for item in acks)
    assert all("SENSITIVE_SCENE_CHUNK" not in str(item) for item in acks)


def test_overlay_transfer_progress_uses_its_own_id_and_ack_without_scene_payload(monkeypatch):
    extension, acks = _extension_without_kit(monkeypatch)
    extension._overlay_transfer = SceneTransfer(validator=validate_overlay)
    for command, args in [
        ("beginRenderOverlay", {"requestId": "overlay-a", "chunks": 1}),
        ("renderOverlayChunk", {"requestId": "overlay-a", "index": 0, "data": "SENSITIVE_OVERLAY_CHUNK"}),
        ("renderOverlayProgress", {"requestId": "barrier-a", "overlayRequestId": "overlay-a", "received": 1}),
    ]:
        extension._route_command({"command_name": command, "message": json.dumps(args)})
    assert acks == [("barrier-a", {"ok": True, "received": 1})]
    assert extension._overlay_transfer.progress("overlay-a") == 1
    assert extension._render_transfer.current is None
    assert "SENSITIVE_OVERLAY_CHUNK" not in str(acks)
