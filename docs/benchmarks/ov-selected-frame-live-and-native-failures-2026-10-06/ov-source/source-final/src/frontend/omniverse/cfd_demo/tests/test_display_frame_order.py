"""Real router/handlers with CPU blocked display writers; no Kit or GPU."""
import ast
import asyncio
import json
import sys
import types

import pytest

from test_dataset_scene_race import BRIDGE, _harness, _drain_until


def bind_display(monkeypatch, ext):
    namespace = {"__name__": "race_bridge.test", "__package__": "race_bridge",
                 "asyncio": asyncio, "omni": sys.modules["omni"]}
    tree = ast.parse((BRIDGE / "extension.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CfdBridgeExtension")
    names = {"_route_command", "_display_mutation", "_legacy_display", "_set_slice",
             "_volume_display", "_slice_rebuild", "_slice_readback", "_slice_state"}
    for method in cls.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in names:
            exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                         str(BRIDGE / "extension.py"), "exec"), namespace)
            setattr(type(ext), method.name, namespace[method.name])
    ext.OV_DISPLAY_COMMANDS = {"setSlice", "getSliceState", "setVolumeDisplay", "loadResultDataset"}
    for name in ("handlers", "parsers"):
        mod = types.ModuleType("race_bridge." + name)
        mod.__package__ = "race_bridge"
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
        exec(compile((BRIDGE / (name + ".py")).read_text(), str(BRIDGE / (name + ".py")), "exec"), mod.__dict__)
    sys.modules["race_bridge.slice_render"].normalize_plane = lambda o, n: (o, n)
    geom = types.ModuleType("race_bridge.geom")
    geom.moved = lambda a, b: a != b
    monkeypatch.setitem(sys.modules, geom.__name__, geom)


@pytest.mark.parametrize("command", ["setStreamlines", "setSeedSphere", "syncFromViewport",
                                    "setVolumeDisplay", "setSlice", "getSliceState"])
def test_actual_old_writer_finishes_before_next_frame_import(monkeypatch, tmp_path, command):
    async def scenario():
        ext, scene, stage, _, _, granted = _harness(monkeypatch, tmp_path, None)
        bind_display(monkeypatch, ext)
        started, release = asyncio.Event(), asyncio.Event()
        events = []
        async def writer(*args, **kwargs):
            started.set()
            await release.wait()
            events.append("old-write")
            return {"field": "T", "visible": True, "domain": [1, 2]} if command == "setVolumeDisplay" else {"tris": 1}
        scene.generate_streamlines = writer
        scene.update_seed_sphere = writer
        scene.show_volume_display = writer
        scene.update_slice = writer
        scene.seed_center = [0, 0, 0]
        scene._seed_ui_center = [0, 0, 0]
        scene.adopt_dragged_seed = lambda stage: None
        scene.bake_roi_xform = lambda stage: None
        scene.adopt_dragged_slice = lambda stage: object()
        scene.clip_enabled = False
        scene.streamlines_enabled = True
        scene.slice_visible = command in {"setSlice", "getSliceState"}
        scene.slice_origin = [0, 0, 0]
        scene.slice_normal = [0, 1, 0]
        scene.slice_radius = 1
        scene.slice_field = "T"
        scene.slice_domain = None
        scene.resolve_slice_origin = lambda stage, origin: None
        original = scene.load_dataset
        async def load(stage, path):
            events.append("new-import")
            return await original(stage, path)
        scene.load_dataset = load
        args = {"enabled": True, "visible": True}
        if command == "setVolumeDisplay":
            args = {"requestId": "old", "field": "T", "domain": None}
        elif command == "setSlice":
            args = {"requestId": "old", "visible": True, "origin": [0, 0, 0],
                    "normal": [0, 1, 0], "radius": 1, "field": "T", "domain": None}
        elif command == "getSliceState":
            args = {"requestId": "old"}
        ext._route_command({"command_name": command, "message": json.dumps(args)})
        await _drain_until(started.is_set)
        ext._route_render_command("loadResultDataset", json.dumps({"requestId": "new", "grant": granted}))
        for _ in range(10):
            await asyncio.sleep(0)
        assert "new-import" not in events, (command, events)
        release.set()
        await _drain_until(lambda: any(a["requestId"] == "new" for a in ext.acks))
        assert events.index("old-write") < events.index("new-import")
        assert next(a for a in ext.acks if a["requestId"] == "new")["ok"] is True
    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["setStreamlines", "setSeedSphere", "syncFromViewport"])
def test_queued_old_fire_cannot_touch_a_new_scene(monkeypatch, tmp_path, command):
    async def scenario():
        ext, scene, stage, _, _, _ = _harness(monkeypatch, tmp_path, None)
        bind_display(monkeypatch, ext)
        events = []
        async def writer(*args, **kwargs):
            events.append(stage.project)
        scene.generate_streamlines = writer
        scene.update_seed_sphere = writer
        scene.seed_center = [0, 0, 0]
        scene._seed_ui_center = [0, 0, 0]
        scene.adopt_dragged_seed = lambda stage: events.append("adopt")
        scene.bake_roi_xform = lambda stage: events.append("bake")
        scene.adopt_dragged_slice = lambda stage: None
        scene.clip_enabled = False
        scene.streamlines_enabled = True
        ext._route_command({"command_name": command, "message": json.dumps({"enabled": True, "visible": True})})
        ext._route_render_command("loadRenderScene", json.dumps({"requestId": "scene"}))
        await _drain_until(lambda: len(ext.acks) == 1)
        for _ in range(8):
            await asyncio.sleep(0)
        assert events == []
        assert stage.project == "new-project"
    asyncio.run(scenario())


def test_dispatch_order_preserves_fire_before_load_without_a_scheduler_yield(monkeypatch, tmp_path):
    async def scenario():
        ext, scene, stage, _, _, granted = _harness(monkeypatch, tmp_path, None)
        bind_display(monkeypatch, ext)
        started, release = asyncio.Event(), asyncio.Event()
        events = []
        async def writer(*args, **kwargs):
            events.append("fire-start")
            started.set()
            await release.wait()
            events.append("fire-finish")
        scene.update_seed_sphere = writer
        scene.seed_center = scene._seed_ui_center = [0, 0, 0]
        scene.adopt_dragged_seed = lambda stage: None
        original = scene.load_dataset
        async def load(stage, path):
            events.append("import")
            return await original(stage, path)
        scene.load_dataset = load
        # The real router creates both tasks synchronously in receipt order.
        ext._route_command({"command_name": "setSeedSphere", "message": "{}"})
        ext._route_render_command("loadResultDataset", json.dumps({"requestId": "new", "grant": granted}))
        await _drain_until(started.is_set)
        for _ in range(8):
            await asyncio.sleep(0)
        assert events == ["fire-start"]
        release.set()
        await _drain_until(lambda: len(ext.acks) == 1)
        assert events == ["fire-start", "fire-finish", "import"]
    asyncio.run(scenario())


def test_legacy_handler_without_scheduler_still_schedules_its_one_coroutine(monkeypatch, tmp_path):
    async def scenario():
        ext, scene, stage, _, _, _ = _harness(monkeypatch, tmp_path, None)
        bind_display(monkeypatch, ext)
        events = []
        async def writer(stage, visible):
            events.append(visible)
        scene.update_seed_sphere = writer
        scene.seed_center = scene._seed_ui_center = [0, 0, 0]
        scene.adopt_dragged_seed = lambda stage: None
        result = sys.modules["race_bridge.handlers"].handle_set_seed_sphere(
            scene, stage, True, [0, 0, 0], 0.25, 7)
        assert result is None
        await _drain_until(lambda: bool(events))
        assert events == [True]
        assert scene.seed_radius == 0.25 and scene.n_seeds == 7
    asyncio.run(scenario())
