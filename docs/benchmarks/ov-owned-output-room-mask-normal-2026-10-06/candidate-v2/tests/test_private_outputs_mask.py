"""Actual source functions and in-memory OpenUSD only: no Kit or converter."""
import ast
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from unittest import mock

import pytest
from pxr import Usd, UsdGeom

TREE = Path(os.environ["OV_PRIVATE_TREE"])
BRIDGE = TREE / "src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge"
sys.path.insert(0, str(BRIDGE.parents[1]))
sys.modules.setdefault("omni", types.ModuleType("omni"))
sys.modules.setdefault("omni.usd", types.ModuleType("omni.usd"))
from dc.cfd_bridge import owned_paths, render_scene, handlers


def source_function(path, name):
    tree = ast.parse(path.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), "exec")


def dataset_writer():
    namespace = {"os": os, "json": json, "__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "scene.py", "write_dataset_info"), namespace)
    return namespace["write_dataset_info"]


def packet():
    material = {"color": [1, 0, 0], "emissive": [0, 0, 0], "opacity": 1,
                "roughness": .4, "metalness": .1, "doubleSided": True, "unlit": True}
    def primitive(uid, visible=True):
        return {"id": uid + "/tri", "modelUid": uid, "name": "Room misleading name",
                "kind": "triangles", "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0],
                "indices": [0, 1, 2], "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "visible": visible, "material": copy.deepcopy(material)}
    return {"schema": "dcdtuni.render-scene/v1", "project": {"uid": "p", "name": "mixed"},
            "coordinateSystem": {"upAxis": "Y", "metersPerUnit": 1, "handedness": "right"},
            "primitives": [primitive("shell"), primitive("hidden-shell", False), primitive("equipment")],
            "textures": {}, "view": {"selectedUids": [], "timeSeconds": None}, "warnings": []}


def authored(tmp_path):
    p = packet()
    p["primitives"][0]["displayRole"] = "room-shell"
    p["primitives"][1]["displayRole"] = "room-shell"
    stage = Usd.Stage.CreateInMemory()
    result = render_scene.author_scene(stage, p, tmp_path)
    scene = types.SimpleNamespace(geo_groups={})
    handlers.install_render_room_shell_paths(scene, stage, result["roomShellPaths"])
    return scene, stage, result, p


def visibility(stage, path):
    return UsdGeom.Imageable(stage.GetPrimAtPath(path)).GetVisibilityAttr().Get()


def test_original_dataset_writer_default_is_owned_and_retains_json_shape():
    observed = []
    class Output(io.StringIO):
        def close(self):
            self.captured = self.getvalue()
    stream = Output()
    def opening(path, *args, **kwargs):
        observed.append(str(path))
        return stream
    def owned_writer(path, payload, label):
        observed.append(str(path))
        stream.captured = json.dumps(payload)
    with mock.patch.dict(os.environ, {}, clear=True), mock.patch("builtins.open", opening), mock.patch("os.replace", lambda a, b: observed.append(str(b))), mock.patch.object(owned_paths, "write_owned_json", owned_writer, create=True):
        dataset_writer()(types.SimpleNamespace(field_stats={"T": {"min": 295.1}}), "owned-source.vtu")
    assert observed and all(str(TREE) in p for p in observed)
    assert json.loads(stream.captured) == {"path": "owned-source.vtu", "fields": {"T": {"min": 295.1}}}


def test_original_dataset_writer_refuses_foreign_override_before_open():
    opened = []
    with mock.patch.dict(os.environ, {"DC_CFD_DATASET_INFO": "/tmp/must-not-be-created.json"}, clear=True), mock.patch("builtins.open", lambda *a, **k: opened.append(a) or io.StringIO()), mock.patch("os.replace"):
        dataset_writer()(types.SimpleNamespace(field_stats={}), "source.vtu")
    assert opened == []


@pytest.mark.parametrize("key,name", [("DC_CFD_DATASET_INFO", "dataset-info.json"), ("DC_CFD_SCENE_TREE", "scene-tree.json")])
def test_metadata_default_override_and_runtime_are_checkout_bound(tmp_path, monkeypatch, key, name):
    target = owned_paths.metadata_path(key, name, {})
    assert Path(target) == TREE / ".codex-tmp/ov-bridge-metadata" / name
    runtime = TREE / ".codex-tmp/test-runtime"
    assert owned_paths.metadata_path(key, name, {"DC_CFD_RUNTIME_DIR": str(runtime)}) == str(runtime / name)
    explicit = TREE / ".codex-tmp/explicit" / name
    monkeypatch.chdir(tmp_path)
    assert owned_paths.metadata_path(key, name, {key: str(explicit)}) == str(explicit)
    for value in ("/tmp/foreign.json", "relative.json", ""):
        with pytest.raises(ValueError):
            owned_paths.metadata_path(key, name, {key: value})
    assert not explicit.exists()


def test_atomic_json_rejects_symlinks_and_preserves_previous_on_serialization_failure(tmp_path):
    import secrets
    dest = TREE / ".codex-tmp/test-output" / secrets.token_hex(8) / "result.json"
    owned_paths.write_owned_json(str(dest), {"fields": {"T": [295, 296]}}, "test")
    original = dest.read_bytes()
    with pytest.raises(TypeError):
        owned_paths.write_owned_json(str(dest), {"bad": object()}, "test")
    assert dest.read_bytes() == original
    assert not list(dest.parent.glob(dest.name + ".tmp*"))
    leaf = dest.parent / "link.json"
    leaf.symlink_to(dest)
    with pytest.raises(ValueError):
        owned_paths.write_owned_json(str(leaf), {"mutated": True}, "test")
    assert dest.read_bytes() == original
    parent = dest.parent / "directory-alias"
    parent.symlink_to(dest.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        owned_paths.write_owned_json(str(parent / "new.json"), {}, "test")
    assert not (dest.parent / "new.json").exists()


def test_managed_environment_replaces_overrides_in_actual_child(tmp_path):
    namespace = {"Path": Path}
    path = TREE / "src/frontend/omniverse/cfd_demo/scripts/supervise_kit.py"
    exec(source_function(path, "_owned_metadata_environment"), namespace)
    root = tmp_path / "checkout"; root.mkdir()
    runtime = root / ".codex-tmp/runtime"; runtime.mkdir(parents=True)
    env = namespace["_owned_metadata_environment"]({"DC_CFD_DATASET_INFO": "/tmp/inherited.json", "DC_CFD_SCENE_TREE": "/tmp/inherited-tree.json"}, runtime, root)
    child_env = dict(os.environ); child_env.update(env)
    child = subprocess.run([sys.executable, "-c", "import os,json;print(json.dumps({k:os.environ[k] for k in ('DC_CFD_DATASET_INFO','DC_CFD_SCENE_TREE')}))"], cwd=tmp_path, env=child_env, capture_output=True, check=True)
    assert json.loads(child.stdout) == {"DC_CFD_DATASET_INFO": str(runtime / "dataset-info.json"), "DC_CFD_SCENE_TREE": str(runtime / "scene-tree.json")}
    with pytest.raises(RuntimeError):
        namespace["_owned_metadata_environment"]({}, tmp_path / "foreign", root)


def test_old_packet_stays_valid_but_unknown_display_role_is_rejected():
    render_scene.validate_snapshot(packet())
    p = packet(); p["primitives"][0]["displayRole"] = "equipment"
    with pytest.raises(ValueError):
        render_scene.validate_snapshot(p)


def test_authoring_returns_exact_shell_paths_and_keeps_geometry_visible_flags(tmp_path):
    scene, stage, result, p = authored(tmp_path)
    expected = result["uidPaths"]["shell"] + result["uidPaths"]["hidden-shell"]
    assert result["roomShellPaths"] == expected
    for path in expected:
        assert stage.GetPrimAtPath(path).GetCustomDataByKey("dcdtuni:displayRole") == "room-shell"
    assert visibility(stage, expected[1]) == UsdGeom.Tokens.invisible
    assert "equipment" not in [stage.GetPrimAtPath(x).GetCustomDataByKey("dcdtuni:modelUid") for x in expected]


def test_actual_mask_hides_only_shell_and_off_restores_user_hidden(tmp_path):
    scene, stage, result, p = authored(tmp_path)
    shell = result["uidPaths"]["shell"][0]; hidden = result["uidPaths"]["hidden-shell"][0]
    equipment = result["uidPaths"]["equipment"][0]
    original_equipment = stage.GetPrimAtPath(equipment).GetAttribute("points").Get()
    handlers._set_geometry_hidden(scene, stage, True)
    handlers._set_geometry_hidden(scene, stage, True)
    assert visibility(stage, shell) == visibility(stage, hidden) == UsdGeom.Tokens.invisible
    assert visibility(stage, equipment) != UsdGeom.Tokens.invisible
    handlers._set_geometry_hidden(scene, stage, False)
    assert visibility(stage, shell) != UsdGeom.Tokens.invisible
    assert visibility(stage, hidden) == UsdGeom.Tokens.invisible
    assert stage.GetPrimAtPath(equipment).GetAttribute("points").Get() == original_equipment


def test_reauthor_updates_mask_without_old_visibility_leaking_into_new_scene(tmp_path):
    scene, stage, result, p = authored(tmp_path)
    handlers._set_geometry_hidden(scene, stage, True)
    # Same project, same hashed path, but user has now hidden the originally visible shell.
    p["primitives"][0]["visible"] = False
    p["primitives"][1]["id"] = "new-shell/tri"
    p["primitives"][1]["visible"] = True
    new = render_scene.author_scene(stage, p, tmp_path)
    handlers.install_render_room_shell_paths(scene, stage, new["roomShellPaths"])
    assert visibility(stage, new["uidPaths"]["hidden-shell"][0]) == UsdGeom.Tokens.invisible
    handlers._set_geometry_hidden(scene, stage, False)
    assert visibility(stage, new["uidPaths"]["shell"][0]) == UsdGeom.Tokens.invisible
    assert visibility(stage, new["uidPaths"]["hidden-shell"][0]) != UsdGeom.Tokens.invisible
    assert not getattr(scene, "_volume_geometry_saved", {})


def test_project_and_frame_cleanup_restore_before_new_install(tmp_path):
    scene, stage, result, p = authored(tmp_path)
    handlers._set_geometry_hidden(scene, stage, True)
    handlers._set_geometry_hidden(scene, stage, False)
    p["project"]["uid"] = "another"
    p["primitives"] = [p["primitives"][2]]
    new = render_scene.author_scene(stage, p, tmp_path)
    handlers.install_render_room_shell_paths(scene, stage, new["roomShellPaths"])
    assert not scene._volume_geometry_hidden
    assert scene._render_room_shell_paths == ()
    handlers._set_geometry_hidden(scene, stage, False)
    assert visibility(stage, new["uidPaths"]["equipment"][0]) != UsdGeom.Tokens.invisible


def test_legacy_mask_off_does_not_reveal_preexisting_hidden_room():
    stage = Usd.Stage.CreateInMemory()
    a = UsdGeom.Xform.Define(stage, "/World/Room")
    a.MakeInvisible()
    scene = types.SimpleNamespace(geo_groups={"Room": ["/World/Room"]})
    handlers._set_geometry_hidden(scene, stage, True)
    handlers._set_geometry_hidden(scene, stage, False)
    assert a.ComputeVisibility() == UsdGeom.Tokens.invisible


def test_original_mask_sees_authoritative_render_paths_even_without_legacy_room_group(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    result = render_scene.author_scene(stage, packet(), tmp_path)
    shell = result["uidPaths"]["shell"][0]
    scene = types.SimpleNamespace(geo_groups={}, _render_room_shell_paths=(shell,))
    handlers._set_geometry_hidden(scene, stage, True)
    assert visibility(stage, shell) == UsdGeom.Tokens.invisible


def test_cache_and_converter_outputs_reject_escape_before_library_work(tmp_path):
    cache = owned_paths.bridge_cache_directory({})
    assert Path(cache) == TREE / ".codex-tmp/ov-bridge-cache"
    runtime = TREE / ".codex-tmp/cache-runtime"
    assert owned_paths.bridge_cache_directory({"DC_CFD_RUNTIME_DIR": str(runtime)}) == str(runtime / "dataset-cache")
    namespace = {"os": os, "__package__": "dc.cfd_bridge"}
    # Actual converter functions hit ownership before importing VTK or entering to_thread.
    import asyncio
    for name in ("_convert_vtm", "_convert_case"):
        exec(source_function(BRIDGE / "scene.py", name), namespace)
        with pytest.raises(ValueError, match="inside this checkout"):
            asyncio.run(namespace[name](object(), "/tmp/not-a-business-input.case"))


def test_scene_tree_writer_matches_dataset_default_and_rejects_override():
    namespace = {"metadata_path": owned_paths.metadata_path, "write_owned_json": owned_paths.write_owned_json}
    exec(source_function(BRIDGE / "scenetree.py", "write_manifest"), namespace)
    with mock.patch.dict(os.environ, {}, clear=True):
        path = namespace["write_manifest"]({"groups": []})
    assert Path(path) == TREE / ".codex-tmp/ov-bridge-metadata/scene-tree.json"
    assert json.loads(Path(path).read_bytes()) == {"groups": []}
    with pytest.raises(ValueError):
        namespace["write_manifest"]({}, "/tmp/never-created.json")


def extension_author(scene, stage):
    import asyncio
    namespace = {"__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "extension.py", "_author_render_scene"), namespace)
    extension = types.SimpleNamespace(
        _display_commit_lock=asyncio.Lock(), _display_generation=1, _display_project_uid="p",
        _scene=scene, _render_uid_paths={}, _render_room_shell_paths=(), _render_primitive_ids={},
        _liquid=None, _scene_revision=0, _apply_render_view=lambda stage, view: {"view": view})
    return extension, namespace["_author_render_scene"]


def test_actual_extension_installs_mask_registry_with_late_scene_and_same_project_reauthor(tmp_path):
    import asyncio
    stage = Usd.Stage.CreateInMemory()
    p = packet(); p["primitives"][0]["displayRole"] = "room-shell"
    extension, author = extension_author(None, stage)
    with mock.patch.dict(os.environ, {"DCDTUNI_RENDER_TEXTURE_CACHE": str(TREE / ".codex-tmp/render-textures")}, clear=True):
        result = asyncio.run(author(extension, stage, p, "hash-a", "request-a", 1))
        assert extension._render_room_shell_paths
        scene = types.SimpleNamespace(geo_groups={})
        extension._scene = scene
        # This is the same registration the normal _startup_async invokes.
        handlers.install_render_room_shell_paths(scene, stage, extension._render_room_shell_paths)
        path = extension._render_uid_paths["shell"][0]
        handlers._set_geometry_hidden(scene, stage, True)
        p["primitives"][0]["visible"] = False
        asyncio.run(author(extension, stage, p, "hash-a2", "request-a2", 1))
        assert scene._volume_geometry_hidden
        handlers._set_geometry_hidden(scene, stage, False)
        assert visibility(stage, path) == UsdGeom.Tokens.invisible
        assert result["view"] == p["view"]


def test_actual_dataset_frame_load_restores_shell_before_ingestion(tmp_path):
    import asyncio
    namespace = {"__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "scene.py", "load_dataset"), namespace)
    testing = types.ModuleType("omni.cae.testing")
    async def wait_for_update():
        pass
    testing.wait_for_update = wait_for_update
    sys.modules["omni.cae"] = types.ModuleType("omni.cae")
    sys.modules["omni.cae.testing"] = testing
    scene, stage, result, p = authored(tmp_path)
    shell = result["uidPaths"]["shell"][0]; hidden = result["uidPaths"]["hidden-shell"][0]
    handlers._set_geometry_hidden(scene, stage, True)
    observed = []
    async def ingest(stage):
        observed.append([visibility(stage, shell), visibility(stage, hidden)])
        return True
    src = tmp_path / "controlled.vtu"; src.write_text("not converted or ingested by a solver")
    scene.__dict__.update(_loading=False, volume_path="/World/Volume", streamlines_path="/World/Streams",
                          seed_sphere_path="/World/SeedSphere", seed_source_path="/World/SeedSource",
                          _ingest_dataset=ingest, write_dataset_info=lambda path: None)
    assert asyncio.run(namespace["load_dataset"](scene, stage, str(src))) is True
    assert observed == [[UsdGeom.Tokens.inherited, UsdGeom.Tokens.invisible]]
    assert not scene._volume_geometry_hidden


def test_actual_new_project_clear_retains_user_hidden_before_author(tmp_path):
    import asyncio
    namespace = {"__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "extension.py", "_clear_display"), namespace)
    scene, stage, result, p = authored(tmp_path)
    handlers._set_geometry_hidden(scene, stage, True)
    observed = []
    async def noop(stage):
        pass
    async def off(stage, field):
        assert field == "off"
        handlers._set_geometry_hidden(scene, stage, False)
        observed.append(visibility(stage, result["uidPaths"]["hidden-shell"][0]))
    scene.__dict__.update(remove_streamlines=noop, remove_slice=noop, show_volume_display=off)
    extension=types.SimpleNamespace(_scene=scene, _display_project_uid="p")
    asyncio.run(namespace["_clear_display"](extension, stage))
    assert observed == [UsdGeom.Tokens.invisible]
    assert extension._display_project_uid is None
    assert not scene._volume_geometry_saved


def test_actual_legacy_dataset_swap_restores_mask_before_ingestion(tmp_path):
    import asyncio
    namespace = {"__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "scene.py", "load_dataset"), namespace)
    testing = types.ModuleType("omni.cae.testing")
    async def wait_for_update():
        pass
    testing.wait_for_update = wait_for_update
    sys.modules["omni.cae"] = types.ModuleType("omni.cae")
    sys.modules["omni.cae.testing"] = testing
    stage = Usd.Stage.CreateInMemory()
    result = render_scene.author_scene(stage, packet(), tmp_path)
    shell = result["uidPaths"]["shell"][0]; hidden = result["uidPaths"]["hidden-shell"][0]
    scene = types.SimpleNamespace(geo_groups={"Room": [shell, hidden]})
    handlers._set_geometry_hidden(scene, stage, True)
    assert visibility(stage, shell) == UsdGeom.Tokens.invisible
    observed = []
    async def ingest(stage):
        observed.append([visibility(stage, shell), visibility(stage, hidden)])
        return True
    src = tmp_path / "controlled-legacy.vtu"; src.write_text("only source existence is checked")
    scene.__dict__.update(_loading=False, volume_path="/World/Volume", streamlines_path="/World/Streams",
                          seed_sphere_path="/World/SeedSphere", seed_source_path="/World/SeedSource",
                          _ingest_dataset=ingest, write_dataset_info=lambda path: None)
    assert asyncio.run(namespace["load_dataset"](scene, stage, str(src))) is True
    assert observed == [[UsdGeom.Tokens.inherited, UsdGeom.Tokens.invisible]]


def test_actual_project_clear_restores_mask_even_if_result_teardown_raises(tmp_path):
    import asyncio
    namespace = {"__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "extension.py", "_clear_display"), namespace)
    stage = Usd.Stage.CreateInMemory()
    result = render_scene.author_scene(stage, packet(), tmp_path)
    shell = result["uidPaths"]["shell"][0]; hidden = result["uidPaths"]["hidden-shell"][0]
    scene = types.SimpleNamespace(geo_groups={"Room": [shell, hidden]})
    handlers._set_geometry_hidden(scene, stage, True)
    async def broken(stage):
        raise RuntimeError("controlled result teardown failure")
    scene.remove_streamlines = broken
    extension = types.SimpleNamespace(_scene=scene, _display_project_uid="p")
    asyncio.run(namespace["_clear_display"](extension, stage))
    assert visibility(stage, shell) == UsdGeom.Tokens.inherited
    assert visibility(stage, hidden) == UsdGeom.Tokens.invisible
    assert not scene._volume_geometry_saved
