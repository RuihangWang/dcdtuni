"""Independent ownership witnesses against the frozen v2 private source."""
import types
from unittest import mock
import pytest
from pxr import Sdf, Usd, UsdGeom
from test_private_outputs_mask import handlers


def exported(stage, extra=()):
    return tuple(layer.ExportToString() for layer in (stage.GetRootLayer(), stage.GetSessionLayer(), *extra))


def test_blocked_default_exact_spec_and_sublayer_caller_target():
    stage = Usd.Stage.CreateInMemory()
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    weak = Sdf.Layer.CreateAnonymous("independent-weak")
    root.subLayerPaths.append(weak.identifier)
    path = "/World/Room"
    attr = UsdGeom.Xform.Define(stage, path).GetVisibilityAttr()
    attr.Set(UsdGeom.Tokens.inherited, 1)
    attr.Set(UsdGeom.Tokens.invisible, 8)
    spec = root.GetAttributeAtPath(path + ".visibility")
    spec.default = Sdf.ValueBlock()
    spec.documentation = "preserve complete authored property"
    spec.customData = {"review": {"a": "b"}}
    stage.SetEditTarget(weak)
    before = exported(stage, (weak,))
    scene = types.SimpleNamespace(geo_groups={"Room": [path]})
    handlers._set_geometry_hidden(scene, stage, True)
    assert stage.GetEditTarget().GetLayer() == weak
    for time in (Usd.TimeCode.Default(), Usd.TimeCode(-3), Usd.TimeCode(1), Usd.TimeCode(5), Usd.TimeCode(8), Usd.TimeCode(100)):
        assert UsdGeom.Imageable(stage.GetPrimAtPath(path)).ComputeVisibility(time) == UsdGeom.Tokens.invisible
    handlers._set_geometry_hidden(scene, stage, False)
    assert exported(stage, (weak,)) == before
    assert stage.GetEditTarget().GetLayer() == weak


def test_removed_root_model_is_never_resurrected():
    stage = Usd.Stage.CreateInMemory()
    path = "/World/Room"
    UsdGeom.Xform.Define(stage, path).MakeInvisible()
    scene = types.SimpleNamespace(geo_groups={"Room": [path]})
    handlers._set_geometry_hidden(scene, stage, True)
    stage.RemovePrim(path)
    before = exported(stage)
    stage.SetEditTarget(stage.GetSessionLayer())
    handlers._set_geometry_hidden(scene, stage, False)
    assert exported(stage) == before
    assert not stage.GetPrimAtPath(path).IsValid()
    assert not scene._volume_geometry_saved
    assert not scene._volume_geometry_hidden


@pytest.mark.parametrize("failed_index", [0, 1, 2, 3])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_nested_shared_weak_ancestors_retry_restores_exactly(failed_index, reverse_order):
    stage = Usd.Stage.CreateInMemory()
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    weak = Sdf.Layer.CreateAnonymous("independent-nested-weak")
    root.subLayerPaths.append(weak.identifier)
    stage.SetEditTarget(weak)
    paths = ["/World/Shared/A", "/World/Shared/B", "/World/Shared/Deep/C", "/World/Shared/Deep/D"]
    for path in paths:
        UsdGeom.Xform.Define(stage, path)
    stage.SetEditTarget(session)
    before = exported(stage, (weak,))
    order = list(reversed(paths)) if reverse_order else paths
    scene = types.SimpleNamespace(geo_groups={"Room": order})
    handlers._set_geometry_hidden(scene, stage, True)
    restore = handlers._restore_visibility
    fail_path = paths[failed_index]
    def fail_one(stage_arg, snapshot):
        if str(snapshot[2]) == fail_path + ".visibility":
            raise RuntimeError("independent controlled retry fault")
        return restore(stage_arg, snapshot)
    with mock.patch.object(handlers, "_restore_visibility", fail_one):
        with pytest.raises(RuntimeError, match="restore failed"):
            handlers._set_geometry_hidden(scene, stage, False)
    assert set(scene._volume_geometry_saved) == {fail_path}
    assert scene._volume_geometry_hidden
    assert stage.GetEditTarget().GetLayer() == session
    handlers._set_geometry_hidden(scene, stage, False)
    assert exported(stage, (weak,)) == before
    assert not scene._volume_geometry_saved
    assert not scene._volume_geometry_hidden
