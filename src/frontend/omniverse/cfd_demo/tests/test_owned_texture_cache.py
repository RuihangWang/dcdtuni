
"""Owned texture-cache boundary tests; no GPU, Kit or USD process is started."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from test_render_scene_progress import _extension_without_kit

ROOT = Path(__file__).resolve().parents[5]
BRIDGE = ROOT / "src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge"


def helper():
    spec = importlib.util.spec_from_file_location("owned_texture_path_test", BRIDGE / "owned_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_texture_cache_path


def bridge_route(monkeypatch):
    extension, acks = _extension_without_kit(monkeypatch)
    # The actual route sees a ready stage; only author_scene is observed/stopped.
    context = types.SimpleNamespace(get_stage=lambda: object())
    monkeypatch.setattr(sys.modules["omni.usd"], "get_context", lambda: context)
    extension._render_transfer.finish = lambda request: {"primitives": [], "project": {"uid": "owned-cache-test"}}
    # Route now schedules the real author coroutine through the display barrier.
    # Initialise its normal startup state, and only drive scheduling synchronously.
    extension._display_generation = 0
    extension._display_commit_lock = asyncio.Lock()
    extension._display_project_uid = None
    extension._scene = None
    # on_startup also installs the signed-result gate; a scene load invalidates
    # it (ca1cee61f) before the texture cache is resolved, so the route needs
    # the real controller, not a stub that would hide a changed call.
    from dc.cfd_bridge.verified_result_grant import VerifiedResultController
    extension._verified_result_controller = VerifiedResultController()
    extension._trusted_result_context = None

    def complete(request_id, command, coroutine):
        async def wait():
            try:
                result = await coroutine
                extension._render_ack(request_id, ok=True, **result)
            except Exception as error:
                extension._render_ack(request_id, ok=False, error=str(error))
        asyncio.run(wait())

    extension._ack_after = complete
    from dc.cfd_bridge import render_scene, result_layers_usd
    calls = []

    # Clearing the previous result layers is the first live-stage write of a
    # scene load. Record it in the same list so the tests can see whether the
    # cache was resolved before the stage was touched.
    def clear(stage):
        calls.append("clear_result_layers")
        return 0

    def author(stage, packet, cache, **kwargs):
        calls.append(cache)
        raise RuntimeError("test reached the author with validated cache")

    monkeypatch.setattr(result_layers_usd, "clear_result_layers", clear)
    monkeypatch.setattr(render_scene, "author_scene", author)
    return extension, acks, calls


@pytest.mark.parametrize("cache", [None, "relative-textures", "/tmp/dcdtuni-render-textures"])
def test_bridge_rejects_missing_or_outside_cache_before_author(monkeypatch, cache):
    extension, acks, calls = bridge_route(monkeypatch)
    if cache is None:
        monkeypatch.delenv("DCDTUNI_RENDER_TEXTURE_CACHE", raising=False)
    else:
        monkeypatch.setenv("DCDTUNI_RENDER_TEXTURE_CACHE", cache)
    extension._route_render_command("loadRenderScene", json.dumps({"requestId": "cache-boundary"}))
    # Rejected before the stage is touched: the old scene and its result
    # layers stay as they were when the texture cache is misconfigured.
    assert calls == []
    assert len(acks) == 1 and acks[0][0] == "cache-boundary"
    assert acks[0][1]["ok"] is False
    assert "DCDTUNI_RENDER_TEXTURE_CACHE" in acks[0][1]["error"]


def test_bridge_valid_cache_is_forwarded_absolute_without_cli_change(monkeypatch, tmp_path):
    extension, acks, calls = bridge_route(monkeypatch)
    cache = tmp_path / "runtime" / "render-textures"
    monkeypatch.setenv("DCDTUNI_RENDER_TEXTURE_CACHE", str(cache))
    extension._route_render_command("loadRenderScene", json.dumps({"requestId": "owned-cache"}))
    assert calls == ["clear_result_layers", str(cache.resolve())]
    assert acks == [("owned-cache", {"ok": False, "error": "test reached the author with validated cache"})]
    # The check only validates; it must not create files before scene authoring.
    assert not cache.exists()


@pytest.mark.parametrize("environment", [{}, {"DCDTUNI_RENDER_TEXTURE_CACHE": ""},
                                        {"DCDTUNI_RENDER_TEXTURE_CACHE": "relative"},
                                        {"DCDTUNI_RENDER_TEXTURE_CACHE": "/tmp/dcdtuni-render-textures"}])
def test_helper_requires_explicit_absolute_checkout_path(environment):
    with pytest.raises(ValueError, match="DCDTUNI_RENDER_TEXTURE_CACHE"):
        helper()(environment)


def test_helper_rejects_checkout_symlink_escape_without_writing(tmp_path):
    link = tmp_path / "cache-escape"
    link.symlink_to("/", target_is_directory=True)
    with pytest.raises(ValueError, match="DCDTUNI_RENDER_TEXTURE_CACHE.*symlink"):
        helper()({"DCDTUNI_RENDER_TEXTURE_CACHE": str(link)})
    assert link.is_symlink()


def test_helper_is_independent_of_cwd_and_does_not_create_cache(monkeypatch, tmp_path):
    target = tmp_path / "abs-runtime" / "render-textures"
    monkeypatch.chdir("/")
    assert helper()({"DCDTUNI_RENDER_TEXTURE_CACHE": str(target)}) == str(target.resolve())
    assert not target.exists()
