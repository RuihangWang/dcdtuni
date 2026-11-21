"""Real in-memory USD layer composition, not a composed-value mock."""
import asyncio
import types
import sys
from unittest import mock

import pytest
from pxr import Sdf, Usd, UsdGeom
from test_owned_outputs_mask import authored, handlers, render_scene, source_function, BRIDGE


def computed(stage, path, time=None):
    return UsdGeom.Imageable(stage.GetPrimAtPath(path)).ComputeVisibility(
        Usd.TimeCode.Default() if time is None else Usd.TimeCode(time))


def scene_for(path):
    return types.SimpleNamespace(geo_groups={"Room": [path]})


def layer_bytes(*layers):
    return tuple(layer.ExportToString() for layer in layers)


def test_session_caller_mask_root_reauthor_off_restores_new_visible(tmp_path):
    scene, stage, result, packet = authored(tmp_path)
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    path = result["uidPaths"]["shell"][0]
    stage.SetEditTarget(session)
    session_before = session.ExportToString()
    handlers._set_geometry_hidden(scene, stage, True)
    assert stage.GetEditTarget().GetLayer() == session
    new = render_scene.author_scene(stage, packet, tmp_path)
    handlers.install_render_room_shell_paths(scene, stage, new["roomShellPaths"])
    handlers._set_geometry_hidden(scene, stage, False)
    assert computed(stage, path) == UsdGeom.Tokens.inherited
    assert session.ExportToString() == session_before
    assert stage.GetEditTarget().GetLayer() == session


def test_user_session_hidden_is_not_captured_as_root_original(tmp_path):
    scene, stage, result, packet = authored(tmp_path)
    path = result["uidPaths"]["shell"][0]
    session = stage.GetSessionLayer()
    stage.SetEditTarget(session)
    UsdGeom.Imageable(stage.GetPrimAtPath(path)).MakeInvisible()
    original_session = session.ExportToString()
    new = render_scene.author_scene(stage, packet, tmp_path)
    original_root = stage.GetRootLayer().ExportToString()
    handlers.install_render_room_shell_paths(scene, stage, new["roomShellPaths"])
    handlers._set_geometry_hidden(scene, stage, True)
    handlers._set_geometry_hidden(scene, stage, False)
    assert computed(stage, path) == UsdGeom.Tokens.invisible
    assert layer_bytes(stage.GetRootLayer(), session) == (original_root, original_session)
    assert stage.GetEditTarget().GetLayer() == session


def test_root_samples_and_property_metadata_restore_exactly():
    stage = Usd.Stage.CreateInMemory()
    path = "/World/Room"
    attr = UsdGeom.Xform.Define(stage, path).GetVisibilityAttr()
    attr.Set(UsdGeom.Tokens.inherited)
    attr.Set(UsdGeom.Tokens.inherited, 0)
    attr.Set(UsdGeom.Tokens.inherited, 10)
    attr.SetDocumentation("original authored animation")
    attr.SetCustomData({"ownership": "user"})
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    original = layer_bytes(root, session)
    stage.SetEditTarget(session)
    scene = scene_for(path)
    handlers._set_geometry_hidden(scene, stage, True)
    for time in (None, -1, 0, 5, 10, 12):
        assert computed(stage, path, time) == UsdGeom.Tokens.invisible
    assert root.ListTimeSamplesForPath(Sdf.Path(path + ".visibility")) == []
    handlers._set_geometry_hidden(scene, stage, False)
    assert layer_bytes(root, session) == original
    for time in (None, 0, 5, 10):
        assert computed(stage, path, time) == UsdGeom.Tokens.inherited
    assert stage.GetEditTarget().GetLayer() == session


def test_absent_root_property_with_session_hidden_remains_absent():
    stage = Usd.Stage.CreateInMemory()
    path = "/World/Room"
    UsdGeom.Xform.Define(stage, path)
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    stage.SetEditTarget(session)
    UsdGeom.Imageable(stage.GetPrimAtPath(path)).MakeInvisible()
    before = layer_bytes(root, session)
    scene = scene_for(path)
    handlers._set_geometry_hidden(scene, stage, True)
    handlers._set_geometry_hidden(scene, stage, False)
    assert layer_bytes(root, session) == before
    assert not root.GetAttributeAtPath(path + ".visibility")
    assert computed(stage, path) == UsdGeom.Tokens.invisible


def test_sublayer_only_prim_does_not_leak_root_parent_containers():
    stage = Usd.Stage.CreateInMemory()
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    weak = Sdf.Layer.CreateAnonymous("weak")
    root.subLayerPaths.append(weak.identifier)
    stage.SetEditTarget(weak)
    path = "/World/OnlyWeak/Room"
    UsdGeom.Xform.Define(stage, path)
    stage.SetEditTarget(session)
    before = layer_bytes(root, session, weak)
    scene = scene_for(path)
    handlers._set_geometry_hidden(scene, stage, True)
    assert computed(stage, path, 7) == UsdGeom.Tokens.invisible
    handlers._set_geometry_hidden(scene, stage, False)
    assert layer_bytes(root, session, weak) == before
    assert not root.GetPrimAtPath("/World")
    assert stage.GetEditTarget().GetLayer() == session


@pytest.mark.parametrize("sampled", [False, True])
@pytest.mark.parametrize("in_sublayer", [False, True])
def test_stronger_visible_opinion_refuses_and_rolls_back(sampled, in_sublayer):
    stage = Usd.Stage.CreateInMemory()
    path = "/World/Room"
    UsdGeom.Xform.Define(stage, path)
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    strong = Sdf.Layer.CreateAnonymous("strong") if in_sublayer else session
    if in_sublayer:
        session.subLayerPaths.append(strong.identifier)
    stage.SetEditTarget(strong)
    attr = UsdGeom.Imageable(stage.GetPrimAtPath(path)).GetVisibilityAttr()
    attr.Set(UsdGeom.Tokens.inherited, Usd.TimeCode(10) if sampled else Usd.TimeCode.Default())
    stage.SetEditTarget(session)
    before = layer_bytes(root, session, strong)
    scene = scene_for(path)
    with pytest.raises(RuntimeError, match="stronger visibility"):
        handlers._set_geometry_hidden(scene, stage, True)
    assert layer_bytes(root, session, strong) == before
    assert not getattr(scene, "_volume_geometry_saved", {})
    assert not getattr(scene, "_volume_geometry_hidden", False)
    assert stage.GetEditTarget().GetLayer() == session


def test_later_path_refusal_rolls_back_earlier_path_and_parent_containers():
    stage = Usd.Stage.CreateInMemory()
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    weak = Sdf.Layer.CreateAnonymous("weak")
    root.subLayerPaths.append(weak.identifier)
    stage.SetEditTarget(weak)
    for p in ("/World/A/Room", "/World/B/Room"):
        UsdGeom.Xform.Define(stage, p)
    stage.SetEditTarget(session)
    UsdGeom.Imageable(stage.GetPrimAtPath("/World/B/Room")).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    before = layer_bytes(root, session, weak)
    scene = types.SimpleNamespace(geo_groups={"Room": ["/World/A/Room", "/World/B/Room"]})
    with pytest.raises(RuntimeError, match="stronger visibility"):
        handlers._set_geometry_hidden(scene, stage, True)
    assert layer_bytes(root, session, weak) == before
    assert not getattr(scene, "_volume_geometry_saved", {})


def test_actual_volume_handler_does_not_rebuild_or_ack_when_mask_refused():
    namespace = {"__package__": "dc.cfd_bridge"}
    exec(source_function(BRIDGE / "scene.py", "show_volume_display"), namespace)
    stage = Usd.Stage.CreateInMemory()
    path = "/World/Room"
    UsdGeom.Xform.Define(stage, path)
    stage.SetEditTarget(stage.GetSessionLayer())
    UsdGeom.Imageable(stage.GetPrimAtPath(path)).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    called = []
    async def rebuild(*a, **k):
        called.append("rebuild")
        raise AssertionError("mask refusal must happen before actual rebuild")
    scene = scene_for(path)
    scene.__dict__.update(dataset_path="controlled", field_stats={}, _volume_domain_override={},
                          clip_enabled=False, _rebuild_volume=rebuild)
    slice_render = types.ModuleType("dc.cfd_bridge.slice_render")
    slice_render.volume_display_domain = lambda *a: None
    with mock.patch.dict(sys.modules, {"dc.cfd_bridge.slice_render": slice_render}):
        with pytest.raises(RuntimeError, match="stronger visibility"):
            asyncio.run(namespace["show_volume_display"](scene, stage, "T"))
    assert called == []


def test_restore_failure_attempts_other_paths_retains_retry_and_caller_target(tmp_path):
    scene, stage, result, packet = authored(tmp_path)
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    shell = result["uidPaths"]["shell"][0]
    hidden = result["uidPaths"]["hidden-shell"][0]
    UsdGeom.Imageable(stage.GetPrimAtPath(shell)).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    original = root.ExportToString()
    stage.SetEditTarget(session)
    handlers._set_geometry_hidden(scene, stage, True)
    copy_spec = Sdf.CopySpec
    attempts = []
    def fail_one(source, source_path, destination, destination_path, *args):
        if destination == root and str(destination_path) == shell + ".visibility":
            attempts.append(shell)
            raise RuntimeError("controlled restoration failure")
        return copy_spec(source, source_path, destination, destination_path, *args)
    with mock.patch.object(Sdf, "CopySpec", fail_one):
        with pytest.raises(RuntimeError, match="volume mask restore failed"):
            handlers._set_geometry_hidden(scene, stage, False)
    assert attempts == [shell]
    assert list(scene._volume_geometry_saved) == [shell]
    assert scene._volume_geometry_hidden
    assert computed(stage, hidden) == UsdGeom.Tokens.invisible
    assert stage.GetEditTarget().GetLayer() == session
    handlers._set_geometry_hidden(scene, stage, False)
    assert root.ExportToString() == original
    assert not scene._volume_geometry_saved
    assert not scene._volume_geometry_hidden


def test_repeated_on_failure_retains_earlier_mask_and_does_not_capture_new_path():
    stage = Usd.Stage.CreateInMemory()
    for path in ("/World/A", "/World/B"):
        UsdGeom.Xform.Define(stage, path)
    scene = scene_for("/World/A")
    handlers._set_geometry_hidden(scene, stage, True)
    saved_a = scene._volume_geometry_saved["/World/A"]
    stage.SetEditTarget(stage.GetSessionLayer())
    UsdGeom.Imageable(stage.GetPrimAtPath("/World/B")).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    root_before = stage.GetRootLayer().ExportToString()
    scene.geo_groups["Room"].append("/World/B")
    with pytest.raises(RuntimeError, match="stronger visibility"):
        handlers._set_geometry_hidden(scene, stage, True)
    assert stage.GetRootLayer().ExportToString() == root_before
    assert list(scene._volume_geometry_saved) == ["/World/A"]
    assert scene._volume_geometry_saved["/World/A"] is saved_a
    assert scene._volume_geometry_hidden
    handlers._set_geometry_hidden(scene, stage, False)
    assert computed(stage, "/World/A") == UsdGeom.Tokens.inherited


def test_new_stage_same_path_does_not_receive_old_visibility_snapshot():
    old_stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(old_stage, "/World/Room").MakeInvisible()
    old_root = old_stage.GetRootLayer().ExportToString()
    scene = scene_for("/World/Room")
    handlers._set_geometry_hidden(scene, old_stage, True)
    new_stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(new_stage, "/World/Room")
    new_root = new_stage.GetRootLayer().ExportToString()
    new_stage.SetEditTarget(new_stage.GetSessionLayer())
    handlers.install_render_room_shell_paths(scene, new_stage, ())
    handlers._set_geometry_hidden(scene, new_stage, False)
    assert old_stage.GetRootLayer().ExportToString() == old_root
    assert new_stage.GetRootLayer().ExportToString() == new_root
    assert computed(new_stage, "/World/Room") == UsdGeom.Tokens.inherited
    assert new_stage.GetEditTarget().GetLayer() == new_stage.GetSessionLayer()


def test_root_pruning_preserves_unrelated_later_authored_property():
    stage = Usd.Stage.CreateInMemory()
    weak = Sdf.Layer.CreateAnonymous("weak")
    stage.GetRootLayer().subLayerPaths.append(weak.identifier)
    stage.SetEditTarget(weak)
    UsdGeom.Xform.Define(stage, "/World/A/Room")
    stage.SetEditTarget(stage.GetSessionLayer())
    scene = scene_for("/World/A/Room")
    handlers._set_geometry_hidden(scene, stage, True)
    stage.SetEditTarget(stage.GetRootLayer())
    stage.GetPrimAtPath("/World/A").CreateAttribute("userNote", Sdf.ValueTypeNames.String).Set("keep")
    stage.SetEditTarget(stage.GetSessionLayer())
    handlers._set_geometry_hidden(scene, stage, False)
    assert stage.GetPrimAtPath("/World/A").GetAttribute("userNote").Get() == "keep"
    assert not stage.GetRootLayer().GetAttributeAtPath("/World/A/Room.visibility")
    assert not stage.GetRootLayer().GetPrimAtPath("/World/A/Room")
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()


def test_failed_reauthor_mask_does_not_claim_retired_root_mask_active(tmp_path):
    scene, stage, result, packet = authored(tmp_path)
    handlers._set_geometry_hidden(scene, stage, True)
    path = result["uidPaths"]["shell"][0]
    stage.SetEditTarget(stage.GetSessionLayer())
    UsdGeom.Imageable(stage.GetPrimAtPath(path)).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    new = render_scene.author_scene(stage, packet, tmp_path)
    root_before = stage.GetRootLayer().ExportToString()
    session_before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(RuntimeError, match="stronger visibility"):
        handlers.install_render_room_shell_paths(scene, stage, new["roomShellPaths"])
    assert layer_bytes(stage.GetRootLayer(), stage.GetSessionLayer()) == (root_before, session_before)
    assert not scene._volume_geometry_saved
    assert not scene._volume_geometry_hidden
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()


def weak_siblings():
    stage = Usd.Stage.CreateInMemory()
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    weak = Sdf.Layer.CreateAnonymous("weak")
    root.subLayerPaths.append(weak.identifier)
    stage.SetEditTarget(weak)
    paths = ["/World/Weak/A", "/World/Weak/B"]
    for path in paths:
        UsdGeom.Xform.Define(stage, path)
    stage.SetEditTarget(session)
    scene = types.SimpleNamespace(geo_groups={"Room": paths})
    return scene, stage, weak


def test_shared_weak_parent_is_removed_after_restoring_multiple_siblings():
    scene, stage, weak = weak_siblings()
    before = layer_bytes(stage.GetRootLayer(), stage.GetSessionLayer(), weak)
    handlers._set_geometry_hidden(scene, stage, True)
    handlers._set_geometry_hidden(scene, stage, False)
    assert layer_bytes(stage.GetRootLayer(), stage.GetSessionLayer(), weak) == before


@pytest.mark.parametrize("during_on", [False, True])
def test_failed_restore_keeps_shared_ancestor_cleanup_for_later_retry(during_on):
    scene, stage, weak = weak_siblings()
    before = layer_bytes(stage.GetRootLayer(), stage.GetSessionLayer(), weak)
    if during_on:
        # Strong visible B forces rollback, whose B restoration fails once.
        UsdGeom.Imageable(stage.GetPrimAtPath("/World/Weak/B")).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
        before = layer_bytes(stage.GetRootLayer(), stage.GetSessionLayer(), weak)
    else:
        handlers._set_geometry_hidden(scene, stage, True)
    restoring = handlers._restore_visibility
    def fail_b_once(stage_arg, snapshot):
        if str(snapshot[2]) == "/World/Weak/B.visibility":
            raise RuntimeError("controlled B restore failure")
        return restoring(stage_arg, snapshot)
    with mock.patch.object(handlers, "_restore_visibility", fail_b_once):
        with pytest.raises(RuntimeError, match="rollback failed" if during_on else "restore failed"):
            handlers._set_geometry_hidden(scene, stage, during_on)
    assert list(scene._volume_geometry_saved) == ["/World/Weak/B"]
    assert scene._volume_geometry_hidden
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()
    handlers._set_geometry_hidden(scene, stage, False)
    assert layer_bytes(stage.GetRootLayer(), stage.GetSessionLayer(), weak) == before
