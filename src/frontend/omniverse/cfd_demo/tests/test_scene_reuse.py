"""Back to OV with the scene unchanged: Kit shows the scene it holds again.

Each OV switch used to send and author the whole scene (DAML 15 s, Shinaide
~2 min). A load now records the browser's scene key; reuseRenderScene shows
that scene for a new display session -- old display layers cleared, the view
applied, a new scene revision -- and refuses any other scene.
"""
import asyncio
import json
import sys

from test_dataset_scene_race import _drain_until, _harness

KEY = "b" * 64
HASH = "c" * 64
VIEW = {"selectedUids": [], "timeSeconds": None}


def _load(ext, request_id, **extra):
    ext._route_render_command("loadRenderScene", json.dumps({"requestId": request_id, "projectHash": HASH, **extra}))


def test_a_held_scene_is_reused_and_any_other_refused(monkeypatch, tmp_path):
    async def scenario():
        ext, _scene, stage, *_ = _harness(monkeypatch, tmp_path, None)
        views = []
        sys.modules["race_bridge.render_scene"].apply_view = lambda target, view, contract=None: views.append(view)
        _load(ext, "load-1", sceneKey=KEY)
        await _drain_until(lambda: any(a["requestId"] == "load-1" for a in ext.acks))
        loaded = next(a for a in ext.acks if a["requestId"] == "load-1")
        assert loaded["ok"] and ext._render_scene_key == KEY

        ext._route_render_command("reuseRenderScene", json.dumps(
            {"requestId": "reuse-1", "sceneKey": KEY, "projectHash": HASH, "view": VIEW}))
        await _drain_until(lambda: any(a["requestId"] == "reuse-1" for a in ext.acks))
        reused = next(a for a in ext.acks if a["requestId"] == "reuse-1")
        assert reused["ok"] is True
        assert reused["projectHash"] == HASH
        assert reused["sceneRevision"] == loaded["sceneRevision"] + 1
        assert ext._last_scene_request_id == "reuse-1"
        assert views == [VIEW]
        assert ("scene", "new-project") in stage.events and stage.events.count(("scene", "new-project")) == 1

        for request_id, key, project in (("other-key", "d" * 64, HASH), ("other-project", KEY, "e" * 64)):
            ext._route_render_command("reuseRenderScene", json.dumps(
                {"requestId": request_id, "sceneKey": key, "projectHash": project, "view": VIEW}))
            refused = next(a for a in ext.acks if a["requestId"] == request_id)
            assert refused == {"requestId": request_id, "ok": False, "error": "scene-not-held"}

        # A load without a key forgets the old one: nothing to reuse after it.
        _load(ext, "load-2")
        await _drain_until(lambda: any(a["requestId"] == "load-2" for a in ext.acks))
        assert ext._render_scene_key is None
        ext._route_render_command("reuseRenderScene", json.dumps(
            {"requestId": "reuse-2", "sceneKey": KEY, "projectHash": HASH, "view": VIEW}))
        assert next(a for a in ext.acks if a["requestId"] == "reuse-2")["error"] == "scene-not-held"

    asyncio.run(scenario())


def test_a_reuse_waits_for_the_scene_handed_off_and_still_being_authored(monkeypatch, tmp_path):
    """The page's reuse request can arrive while Kit is still authoring the
    scene the server handed off (scene_handoff): it waits, then reuses it."""
    async def scenario():
        ext, _scene, stage, *_ = _harness(monkeypatch, tmp_path, None)
        sys.modules["race_bridge.render_scene"].apply_view = lambda target, view, contract=None: None
        gate = asyncio.Event()
        author = ext._author_render_scene

        async def held_author(*args, **kwargs):
            await gate.wait()
            return await author(*args, **kwargs)

        packet = {"project": {"uid": "new-project"}, "primitives": [], "view": VIEW}
        generation = ext._begin_display_change()
        ext._render_scene_key = None
        task = asyncio.ensure_future(held_author(stage, packet, HASH, "handoff-1", generation, KEY))
        ext._scene_handoff = {"sceneKey": KEY, "projectHash": HASH, "task": task}
        ext._route_render_command("reuseRenderScene", json.dumps(
            {"requestId": "reuse-early", "sceneKey": KEY, "projectHash": HASH, "view": VIEW}))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not any(a["requestId"] == "reuse-early" for a in ext.acks)  # waiting, not refused
        gate.set()
        await _drain_until(lambda: any(a["requestId"] == "reuse-early" for a in ext.acks))
        reused = next(a for a in ext.acks if a["requestId"] == "reuse-early")
        assert reused["ok"] is True and ext._render_scene_key == KEY
        assert ext._last_scene_request_id == "reuse-early"
        # Another scene is still refused at once.
        ext._route_render_command("reuseRenderScene", json.dumps(
            {"requestId": "reuse-other", "sceneKey": "d" * 64, "projectHash": HASH, "view": VIEW}))
        assert next(a for a in ext.acks if a["requestId"] == "reuse-other")["error"] == "scene-not-held"

    asyncio.run(scenario())
