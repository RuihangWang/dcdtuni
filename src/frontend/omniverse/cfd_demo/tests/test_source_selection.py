"""Real source methods and CPU USD; controlled Kit event/selection carriers.

These tests prove the source-to-UID contract, not streamed pixel picking.
"""
import ast
import json
from pathlib import Path
import sys
import types
import unittest
from pxr import Usd, UsdGeom

BRIDGE = Path(__file__).resolve().parents[1] / "kit/extensions/dc.cfd_bridge/dc/cfd_bridge"


def harness(source=None):
    stage = Usd.Stage.CreateInMemory()
    paths = {uid: [f"/World/RenderScene/Geometry/{uid}/face{i}" for i in range(2)]
             for uid in ("UPS_Rack", "CDU_1")}
    primitive_ids = {}
    for uid, children in paths.items():
        UsdGeom.Xform.Define(stage, children[0].rsplit("/", 1)[0])
        for i, path in enumerate(children):
            prim = UsdGeom.Mesh.Define(stage, path).GetPrim()
            prim.SetCustomDataByKey("dcdtuni:primitiveId", f"{uid}-face{i}")
            prim.SetCustomDataByKey("dcdtuni:modelUid", uid)
            primitive_ids[f"{uid}-face{i}"] = uid
    selection = types.SimpleNamespace(paths=[])
    selection.get_selected_prim_paths = lambda: list(selection.paths)
    selection.set_selected_prim_paths = lambda value, _expand: setattr(selection, "paths", list(value))
    context = types.SimpleNamespace(get_stage=lambda: stage, get_selection=lambda: selection)
    events = []
    bus = types.SimpleNamespace(push=lambda _signal, payload: events.append(payload))
    omni = types.SimpleNamespace(usd=types.SimpleNamespace(get_context=lambda: context),
        kit=types.SimpleNamespace(app=types.SimpleNamespace(get_app=lambda:
            types.SimpleNamespace(get_message_bus_event_stream=lambda: bus))))
    carb = types.SimpleNamespace(events=types.SimpleNamespace(type_from_string=lambda name: name))
    package = types.ModuleType("selection_test_bridge")
    module = types.ModuleType("selection_test_bridge.render_scene")
    module.CAMERA = "/World/RenderCamera"
    sys.modules[package.__name__] = package
    sys.modules[module.__name__] = module
    namespace = {"__package__": package.__name__, "json": json, "omni": omni, "carb": carb}
    tree = ast.parse(Path(source or BRIDGE / "extension.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CfdBridgeExtension")
    methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name in ("_emit_source_pick", "_apply_render_view", "_trace_source_selection")]
    unit = ast.Module(body=[ast.ClassDef(name="Extension", bases=[], keywords=[],
        body=methods, decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(unit), str(source or BRIDGE / "extension.py"), "exec"), namespace)
    extension = namespace["Extension"]()
    extension.__dict__.update(_suppress_ov_pick=False, _render_project_hash="a" * 64,
        _scene_revision=7, _last_ov_pick=None, _render_uid_paths=paths,
        _render_primitive_ids=primitive_ids)
    extension._prepare_output_layout = lambda _stage: None
    extension._observe_grid_viewport = lambda: None
    extension._ensure_grid_raster = lambda: types.SimpleNamespace(display_receipt=lambda _stage, _view: {}, offer=lambda *_args: None)
    return extension, selection, stage, paths, events


def emitted_uids(event):
    if event["action"]["type"] == "selection":
        return [item["modelUid"] for item in event["selection"]]
    return [event["modelUid"]]


class SourceSelectionTests(unittest.TestCase):
    def test_shift_append_after_the_same_first_primitive_reaches_both_whole_uids(self):
        ext, selection, _stage, paths, events = harness()
        selection.paths = paths["UPS_Rack"][:]
        ext._emit_source_pick()
        selection.paths.append(paths["CDU_1"][0])
        ext._emit_source_pick()
        self.assertEqual(set(emitted_uids(events[-1])), {"UPS_Rack", "CDU_1"})
        self.assertEqual(len(events), 2)
        self.assertEqual(emitted_uids(events[-1]), ["UPS_Rack", "CDU_1"])

    def test_real_native_empty_selection_is_forwarded_as_clear(self):
        ext, selection, _stage, paths, events = harness()
        selection.paths = paths["UPS_Rack"][:]
        ext._emit_source_pick()
        selection.paths = []
        ext._emit_source_pick()
        self.assertEqual(emitted_uids(events[-1]), [])

    def test_complete_uid_set_deduplicates_faces_and_order(self):
        ext, selection, _stage, paths, events = harness()
        selection.paths = paths["UPS_Rack"] + paths["CDU_1"]
        ext._emit_source_pick()
        self.assertEqual(len(events[0]["selection"]), 2)
        selection.paths.reverse()
        ext._emit_source_pick()
        self.assertEqual(len(events), 1)

    def test_delayed_programmatic_echo_is_suppressed_by_the_complete_applied_set(self):
        ext, _selection, stage, _paths, events = harness()
        ext._apply_render_view(stage, {"selectedUids": ["UPS_Rack", "CDU_1"]})
        ext._emit_source_pick()
        self.assertEqual(events, [])
        ext._apply_render_view(stage, {"selectedUids": []})
        ext._emit_source_pick()
        self.assertEqual(events, [])

    def test_programmatic_fourteen_object_locate_echo_does_not_collapse_to_first(self):
        ext, selection, stage, _paths, events = harness()
        uids = [f"Rack_A_{i:02}" for i in range(1, 15)]
        for uid in uids:
            path = f"/World/RenderScene/Geometry/{uid}/face0"
            prim = UsdGeom.Mesh.Define(stage, path).GetPrim()
            prim.SetCustomDataByKey("dcdtuni:primitiveId", uid + "-face0")
            prim.SetCustomDataByKey("dcdtuni:modelUid", uid)
            ext._render_uid_paths[uid] = [path]
            ext._render_primitive_ids[uid + "-face0"] = uid
        ext._apply_render_view(stage, {"selectedUids": uids})
        ext._emit_source_pick()
        self.assertEqual(len(selection.paths), 14)
        self.assertEqual(events, [])

    def test_native_add_after_programmatic_selection_is_not_suppressed(self):
        ext, selection, stage, paths, events = harness()
        ext._apply_render_view(stage, {"selectedUids": ["UPS_Rack"]})
        selection.paths.append(paths["CDU_1"][1])
        ext._emit_source_pick()
        self.assertEqual(set(emitted_uids(events[0])), {"UPS_Rack", "CDU_1"})

    def test_programmatic_hidden_children_do_not_block_later_shift_hit(self):
        ext, selection, stage, paths, events = harness()
        UsdGeom.Imageable(stage.GetPrimAtPath(paths["UPS_Rack"][1])).MakeInvisible()
        ext._apply_render_view(stage, {"selectedUids": ["UPS_Rack"]})
        self.assertEqual(selection.paths, [paths["UPS_Rack"][0]])
        selection.paths.append(paths["CDU_1"][0])
        ext._emit_source_pick()
        self.assertEqual(set(emitted_uids(events[0])), {"UPS_Rack", "CDU_1"})

    def test_failed_programmatic_set_does_not_poison_the_last_native_snapshot(self):
        ext, selection, stage, _paths, _events = harness()
        def refuse(*_args):
            raise RuntimeError("controlled refusal")
        selection.set_selected_prim_paths = refuse
        with self.assertRaises(RuntimeError):
            ext._apply_render_view(stage, {"selectedUids": ["UPS_Rack"]})
        self.assertIsNone(ext._last_ov_pick)
        self.assertFalse(ext._suppress_ov_pick)

    def test_parent_visibility_is_computed_by_actual_cpu_usd(self):
        ext, selection, stage, paths, events = harness()
        selection.paths = paths["UPS_Rack"] + [paths["CDU_1"][0]]
        parent = stage.GetPrimAtPath(paths["CDU_1"][0].rsplit("/", 1)[0])
        UsdGeom.Imageable(parent).MakeInvisible()
        ext._emit_source_pick()
        self.assertEqual(events, [])

    def test_unknown_mixed_path_does_not_emit_partial_selection_or_clear(self):
        ext, selection, _stage, paths, events = harness()
        for unknown in ("/World/CAE/result", "/World/RenderScene/Geometry/missing"):
            selection.paths = [paths["UPS_Rack"][0], unknown]
            ext._emit_source_pick()
            self.assertEqual(events, [])

    def test_forged_tags_outside_the_accepted_path_map_are_rejected(self):
        ext, selection, stage, _paths, events = harness()
        prim = UsdGeom.Mesh.Define(stage, "/World/RenderScene/Geometry/forged").GetPrim()
        prim.SetCustomDataByKey("dcdtuni:primitiveId", "UPS_Rack-face0")
        prim.SetCustomDataByKey("dcdtuni:modelUid", "UPS_Rack")
        selection.paths = [str(prim.GetPath())]
        ext._emit_source_pick()
        self.assertEqual(events, [])

    def test_scene_revision_changes_retire_previous_set_identity(self):
        ext, selection, _stage, paths, events = harness()
        selection.paths = [paths["UPS_Rack"][0]]
        ext._emit_source_pick()
        ext._scene_revision += 1
        ext._emit_source_pick()
        self.assertEqual([event["sceneRevision"] for event in events], [7, 8])

    def test_overlong_or_mismatched_source_identity_is_not_partial(self):
        ext, selection, stage, paths, events = harness()
        selection.paths = paths["UPS_Rack"] + [paths["CDU_1"][0]]
        prim = stage.GetPrimAtPath(paths["CDU_1"][0])
        for value in ("bad", "x" * 1025):
            prim.SetCustomDataByKey("dcdtuni:primitiveId", value)
            ext._emit_source_pick()
            self.assertEqual(events, [])

    def test_path_count_cap_does_not_clear(self):
        ext, selection, _stage, paths, events = harness()
        selection.paths = [paths["UPS_Rack"][0]] * 65537
        ext._emit_source_pick()
        self.assertEqual(events, [])

    def test_unicode_source_uid_round_trips_inside_utf8_cap(self):
        ext, selection, _stage, paths, events = harness()
        uid = "液" * 256
        children = paths["CDU_1"][:]
        ext._render_uid_paths[uid] = ext._render_uid_paths.pop("CDU_1")
        for path in children:
            prim = _stage.GetPrimAtPath(path)
            prim.SetCustomDataByKey("dcdtuni:modelUid", uid)
            ext._render_primitive_ids[prim.GetCustomDataByKey("dcdtuni:primitiveId")] = uid
        # A genuine bounded single Unicode UID still fits and round trips.
        selection.paths = children
        ext._emit_source_pick()
        self.assertEqual(emitted_uids(events[0]), [uid])

    def test_payload_byte_cap_refuses_the_whole_snapshot(self):
        ext, selection, stage, _paths, events = harness()
        for i in range(70):
            uid = str(i) + "u" * 250
            prim_id = str(i) + "p" * 1010
            path = f"/World/RenderScene/Geometry/p{i}"
            prim = UsdGeom.Mesh.Define(stage, path).GetPrim()
            prim.SetCustomDataByKey("dcdtuni:primitiveId", prim_id)
            prim.SetCustomDataByKey("dcdtuni:modelUid", uid)
            ext._render_primitive_ids[prim_id] = uid
            ext._render_uid_paths[uid] = [path]
            selection.paths.append(path)
        ext._emit_source_pick()
        self.assertEqual(events, [])
        self.assertIsNone(ext._last_ov_pick)
        selection.paths = selection.paths[:1]
        ext._emit_source_pick()
        self.assertEqual(len(events), 1)

    def test_camera_view_fills_native_widget_without_reauthoring_usd_camera(self):
        from unittest.mock import patch
        ext, selection, stage, paths, _events = harness()
        camera = UsdGeom.Camera.Define(stage, "/World/RenderCamera")
        camera.CreateVerticalApertureAttr(20.955)
        camera.CreateHorizontalApertureAttr(20.955 * 1168 / 796)
        camera.CreateFocalLengthAttr(25.3)
        camera.CreateClippingRangeAttr((0.1, 400))
        before = stage.GetRootLayer().ExportToString()
        viewport = types.SimpleNamespace(fill_frame=False,
            camera_path="/World/ExternalCamera", resolution=(1920, 1080))
        utility = types.ModuleType("omni.kit.viewport.utility")
        utility.get_active_viewport = lambda: viewport
        grid_proof = {"phase": "controlled", "gridPixelVerified": False}
        ext._ensure_grid_raster = lambda: types.SimpleNamespace(
            offer=lambda *_args: None, display_receipt=lambda *_args: grid_proof)
        view = {"selectedUids": ["CDU_1"], "camera": {"position": [1, 2, 3],
            "target": [0, 0, 0], "up": [0, 1, 0], "fov": 45,
            "aspect": 1168 / 796, "near": 0.1, "far": 400}}
        view_before = json.dumps(view, sort_keys=True)
        with patch.dict(sys.modules, {utility.__name__: utility}):
            reply = ext._apply_render_view(stage, view)
        self.assertTrue(viewport.fill_frame)
        self.assertEqual(viewport.camera_path, "/World/RenderCamera")
        # Resolution changes belong to Kit's real widget callbacks, not this adapter.
        self.assertEqual(viewport.resolution, (1920, 1080))
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        self.assertEqual(json.dumps(view, sort_keys=True), view_before)
        self.assertEqual(selection.paths, paths["CDU_1"])
        self.assertEqual(reply["sceneRevision"], 7)
        self.assertEqual(reply["projectHash"], "a" * 64)
        self.assertEqual(reply["primitives"], 4)
        self.assertIs(reply["gridDisplay"], grid_proof)

    def test_camera_free_selection_does_not_take_over_external_viewport(self):
        from unittest.mock import patch
        ext, selection, stage, _paths, _events = harness()
        viewport = types.SimpleNamespace(fill_frame=False,
            camera_path="/World/ExternalCamera", resolution=(1920, 1080))
        calls = []
        utility = types.ModuleType("omni.kit.viewport.utility")
        def active_viewport():
            calls.append("native-viewport")
            return viewport
        utility.get_active_viewport = active_viewport
        ext._ensure_grid_raster = lambda: types.SimpleNamespace(
            offer=lambda *_args: None, display_receipt=lambda *_args: {})
        before = stage.GetRootLayer().ExportToString()
        with patch.dict(sys.modules, {utility.__name__: utility}):
            reply = ext._apply_render_view(stage, {"selectedUids": []})
        self.assertEqual(calls, [])
        self.assertFalse(viewport.fill_frame)
        self.assertEqual(viewport.camera_path, "/World/ExternalCamera")
        self.assertEqual(viewport.resolution, (1920, 1080))
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        self.assertEqual(selection.paths, [])
        self.assertEqual(reply["sceneRevision"], 7)
        self.assertEqual(reply["projectHash"], "a" * 64)



if __name__ == "__main__":
    unittest.main()
