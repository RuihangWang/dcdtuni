"""CPU controlled interleavings of the real Kit dataset/scene commit methods.

Kit-only APIs are fakes; the extension route, dataset loader and importer
discovery execute from their actual AST. No Kit, service, CFD or GPU runs.
"""
import ast
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import types

import pytest

BRIDGE = Path(__file__).resolve().parents[1] / "kit/extensions/dc.cfd_bridge/dc/cfd_bridge"


def _class_methods(file, class_name, names, namespace):
    tree = ast.parse((BRIDGE / file).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    extracted = ast.Module(body=[ast.ClassDef(
        name=class_name, bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(extracted), str(BRIDGE / file), "exec"), namespace)
    return namespace[class_name]


class Prim:
    def __init__(self, path="", kind=""):
        self.path, self.kind = path, kind

    def __bool__(self):
        return bool(self.path)

    def IsValid(self):
        return bool(self.path)

    def GetPath(self):
        return self.path

    def GetTypeName(self):
        return self.kind

    def GetName(self):
        return self.path.rsplit("/", 1)[-1]


class Stage:
    def __init__(self):
        self.project = "old-project"
        self.prims = {}
        self.events = []

    def GetPrimAtPath(self, path):
        return self.prims.get(path, Prim())

    def RemovePrim(self, path):
        self.prims = {p: prim for p, prim in self.prims.items()
                      if p != path and not p.startswith(path + "/")}
        self.events.append(("remove", path, self.project))
        return True

    def Traverse(self):
        return list(self.prims.values())


def _harness(monkeypatch, tmp_path, block):
    started, release = asyncio.Event(), asyncio.Event()
    stage = Stage()
    package = types.ModuleType("race_bridge")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "race_bridge", package)

    def module(name, **values):
        value = types.ModuleType(name)
        value.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    async def wait_for_update():
        await asyncio.sleep(0)

    async def blocked(position):
        if block == position:
            stage.events.append(("blocked", position, stage.project))
            started.set()
            await release.wait()

    async def import_to_stage(*, path, prim_path):
        await blocked("import")
        stage.events.append(("import", path, stage.project))
        stage.prims[prim_path] = Prim(prim_path, "CaeDataSet")
        stage.prims[prim_path + "/T"] = Prim(prim_path + "/T", "CaeField")

    omni = module("omni")
    usd = module("omni.usd")
    omni.usd = usd
    cae = module("omni.cae")
    omni.cae = cae
    cae.testing = module("omni.cae.testing", wait_for_update=wait_for_update)
    cae.importer = module("omni.cae.importer")
    cae.importer.vtk = module("omni.cae.importer.vtk", import_to_stage=import_to_stage)
    selection = types.SimpleNamespace(
        set_selected_prim_paths=lambda *args: None,
        get_selected_prim_paths=lambda: [])
    usd.get_context = lambda: types.SimpleNamespace(
        get_stage=lambda: stage, get_selection=lambda: selection)
    module("pxr", UsdGeom=types.SimpleNamespace(
        Imageable=lambda prim: types.SimpleNamespace(MakeInvisible=lambda: None)))

    def author_scene(target, packet, cache, **kwargs):
        target.project = packet["project"]["uid"]
        target.events.append(("scene", target.project))
        return {"uidPaths": {}, "primitives": 0}

    module("race_bridge.render_scene", author_scene=author_scene, CAMERA="/World/Camera")
    module("race_bridge.owned_paths", render_texture_cache_path=lambda: str(tmp_path / "textures"))
    module("race_bridge.display_grant", verify=lambda grant, *args, **kwargs: grant,
           roots_from_env=lambda value: [str(tmp_path)])
    grid = types.SimpleNamespace(GetBounds=lambda: (0, 1, 0, 1, 0, 1))
    read_grid = lambda path: grid
    module("race_bridge.slice_render", read_grid=read_grid)

    async def executor(_pool, func, *args):
        if func is read_grid:
            await blocked("bounds")
        return func(*args)

    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", executor)
    globals_ = {"__name__": "race_bridge.test", "__package__": "race_bridge",
                "asyncio": asyncio, "json": json, "os": os, "re": re, "omni": omni}
    Extension = _class_methods("extension.py", "CfdBridgeExtension",
        {"_route_render_command", "_load_granted_dataset", "_clear_display", "_ack_after",
         "_author_render_scene", "_apply_render_view"}, dict(globals_))
    Scene = _class_methods("scene.py", "CfdScene",
        {"load_dataset", "_ingest_dataset"}, dict(globals_))
    scene = Scene()
    scene.__dict__.update(
        _loading=False, volume_path="/World/CAE/InternalVolume",
        streamlines_path="/World/CAE/Streamlines", seed_sphere_path="/World/CAE/SeedSphere",
        seed_source_path="/World/CAE/SeedPoints", slice_visible=False,
        dataset_path=None, t_field=None, u_field=None, umag_field=None,
        field_stats=None, ranges={}, vtu_path=None, source_vtu_path=None,
        _slice_grid=None, _slice_cache_key=None, _slice_summary=None)
    path = tmp_path / "old.case"
    path.write_text("fake granted case")

    async def convert(path):
        await blocked("convert")
        return str(tmp_path / "old.vtu")

    async def no_op(*args):
        await asyncio.sleep(0)

    scene._convert_case = convert
    scene.write_dataset_info = lambda path: stage.events.append(("info", path, stage.project))
    scene.remove_streamlines = no_op
    scene.remove_slice = no_op
    scene.show_volume_display = no_op
    ext = Extension()
    ext.__dict__.update(
        _scene=scene, _scene_revision=1, _scene_project_uid="old-project",
        _display_project_uid=None, _render_project_hash="a" * 64,
        _render_uid_paths={}, _render_primitive_ids={}, _last_scene_request_id="before",
        _liquid=None, _liquid_saved={}, _last_ov_pick=None, _suppress_ov_pick=False,
        _display_generation=0, _display_commit_lock=asyncio.Lock())
    ext.acks = []
    ext._render_ack = lambda request_id, **result: ext.acks.append({"requestId": request_id, **result})
    ext._render_transfer = types.SimpleNamespace(finish=lambda request_id: {
        "project": {"uid": "new-project"}, "primitives": [],
        "view": {"camera": None, "selectedUids": []}})
    granted = {"path": str(path), "run": "old-run", "runId": "old-run-id", "time": 0.005}
    return ext, scene, stage, started, release, granted


async def _drain_until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("controlled command did not settle")


@pytest.mark.parametrize("block", ["convert", "import", "bounds"])
def test_blocked_old_dataset_cannot_commit_over_a_new_engineering_scene(monkeypatch, tmp_path, block):
    async def scenario():
        ext, scene, stage, started, release, granted = _harness(monkeypatch, tmp_path, block)
        ext._route_render_command("loadResultDataset",
            json.dumps({"requestId": "old-load", "grant": granted}))
        await _drain_until(started.is_set)
        ext._route_render_command("loadRenderScene", json.dumps({"requestId": "new-scene"}))
        await asyncio.sleep(0)
        release.set()
        await _drain_until(lambda: len(ext.acks) == 2)
        old = next(a for a in ext.acks if a["requestId"] == "old-load")
        new = next(a for a in ext.acks if a["requestId"] == "new-scene")
        assert old["ok"] is False, (block, stage.events, ext.acks)
        assert "scene changed" in old["error"].lower()
        assert new["ok"] is True
        assert stage.project == "new-project"
        assert ext._scene_project_uid == "new-project"
        assert ext._display_project_uid is None
        assert scene.dataset_path is None and scene.t_field is None
        assert scene.vtu_path is None and scene.source_vtu_path is None
        assert scene._slice_grid is None and scene._slice_summary is None
        assert not stage.GetPrimAtPath("/World/internal")
        assert not any(event[0] == "import" and event[-1] == "new-project"
                       for event in stage.events), stage.events
    asyncio.run(scenario())


def test_unchanged_scene_dataset_ack_preserves_grant_run_and_time(monkeypatch, tmp_path):
    async def scenario():
        ext, scene, stage, started, release, granted = _harness(monkeypatch, tmp_path, None)
        ext._route_render_command("loadResultDataset",
            json.dumps({"requestId": "load", "grant": granted}))
        await _drain_until(lambda: len(ext.acks) == 1)
        ack = ext.acks[0]
        assert ack["ok"] is True, ack
        assert ack["dataset"]["runId"] == granted["runId"]
        assert ack["dataset"]["time"] == granted["time"]
        assert ack["dataset"]["hasT"] is True
        assert ext._display_project_uid == "old-project"
        assert scene.dataset_path == "/World/internal"
    asyncio.run(scenario())


def test_queued_old_load_refuses_without_clearing_the_new_scene_result(monkeypatch, tmp_path):
    async def scenario():
        ext, scene, stage, started, release, granted = _harness(monkeypatch, tmp_path, None)
        # All three commands are routed before their coroutine tasks begin.
        ext._route_render_command("loadResultDataset",
            json.dumps({"requestId": "old-queued", "grant": granted}))
        ext._route_render_command("loadRenderScene", json.dumps({"requestId": "new-scene"}))
        new_grant = dict(granted, runId="new-run-id", time=0.01)
        ext._route_render_command("loadResultDataset",
            json.dumps({"requestId": "new-load", "grant": new_grant}))
        await _drain_until(lambda: len(ext.acks) == 3)
        acks = {a["requestId"]: a for a in ext.acks}
        assert acks["old-queued"]["ok"] is False
        assert acks["new-scene"]["ok"] is True
        assert acks["new-load"]["ok"] is True
        assert acks["new-load"]["dataset"]["runId"] == "new-run-id"
        assert acks["new-load"]["dataset"]["time"] == 0.01
        assert ext._display_project_uid == "new-project"
        assert scene.dataset_path == "/World/internal"
        assert stage.GetPrimAtPath("/World/internal")
        assert len([e for e in stage.events if e[0] == "import"]) == 1
    asyncio.run(scenario())


def test_only_latest_scene_commits_after_a_blocked_import(monkeypatch, tmp_path):
    async def scenario():
        ext, scene, stage, started, release, granted = _harness(monkeypatch, tmp_path, "import")
        ext._route_render_command("loadResultDataset",
            json.dumps({"requestId": "old-load", "grant": granted}))
        await _drain_until(started.is_set)
        ext._render_transfer.finish = lambda request_id: {
            "project": {"uid": "middle-project" if request_id == "middle" else "new-project"},
            "primitives": [], "view": {"camera": None, "selectedUids": []}}
        ext._route_render_command("loadRenderScene", json.dumps({"requestId": "middle"}))
        ext._route_render_command("loadRenderScene", json.dumps({"requestId": "latest"}))
        release.set()
        await _drain_until(lambda: len(ext.acks) == 3)
        acks = {a["requestId"]: a for a in ext.acks}
        assert acks["old-load"]["ok"] is False
        assert acks["middle"]["ok"] is False
        assert "superseded" in acks["middle"]["error"]
        assert acks["latest"]["ok"] is True
        assert [e for e in stage.events if e[0] == "scene"] == [("scene", "new-project")]
        assert ext._last_scene_request_id == "latest"
        assert ext._scene_revision == 2
        assert scene.dataset_path is None
    asyncio.run(scenario())


def test_completed_result_survives_same_project_scene_reauthor(monkeypatch, tmp_path):
    async def scenario():
        ext, scene, stage, started, release, granted = _harness(monkeypatch, tmp_path, None)
        ext._route_render_command("loadResultDataset",
            json.dumps({"requestId": "load", "grant": granted}))
        await _drain_until(lambda: len(ext.acks) == 1)
        old_source, old_grid = scene.source_vtu_path, scene._slice_grid
        ext._render_transfer.finish = lambda request_id: {
            "project": {"uid": "old-project"}, "primitives": [],
            "view": {"camera": None, "selectedUids": []}}
        ext._route_render_command("loadRenderScene", json.dumps({"requestId": "same-project"}))
        await _drain_until(lambda: len(ext.acks) == 2)
        assert all(a["ok"] for a in ext.acks)
        assert scene.dataset_path == "/World/internal"
        assert scene.source_vtu_path == old_source and scene._slice_grid is old_grid
        assert ext._display_project_uid == "old-project"
        assert ext._scene_revision == 2
        assert not any(e[0] == "remove" and e[1] == "/World/internal" for e in stage.events)
    asyncio.run(scenario())
