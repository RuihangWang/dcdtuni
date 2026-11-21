"""CPU contract controls, with an explicit public-SDK shim (no Kit/GPU/video).

Controller/geometry/ray arithmetic and bridge routing are real product code;
SDK construction/gesture callbacks are controlled stand-ins, not raster proof.
"""
import ast
import copy
import math
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
EXT = Path(__file__).resolve().parents[1] / 'kit/extensions/dc.cfd_bridge'
sys.path.insert(0, str(EXT))
from dc.cfd_bridge import common_postprocess_previews as p
from dc.cfd_bridge import postprocess_preview_viewport as v


def binding():
    return dict(source=dict(projectUid='project', projectContentSha256='a' * 64,
        cfdrunSha256='b' * 64, meshReceiptSha256='c' * 64, meshQuality='accepted',
        run=dict(uid='run-execution', receiptSha256='d' * 64)), packetSha256='e' * 64,
        timeSeconds=.015, sceneRevision=7, displayGeneration=12, scopeKey='accepted-session', ownerKey='f' * 32)


def packet():
    return dict(schema=p.SCHEMA, binding=binding(), handles=[
        dict(kind='ring', setId=42, origin=[0, 0, 0], normal=[3, 0, 0], radius=2),
        dict(kind='seed', setId=9, centre=[0, 0, 0], radius=1)])


class Frame:
    def __init__(self):
        self.clears = 0
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def clear(self): self.clears += 1


class Shape:
    def __init__(self, positions, *args, **kwargs):
        self.positions, self.args, self.kwargs = copy.deepcopy(positions), args, kwargs


class Curve(Shape):
    CurveType = NS(LINEAR='controlled-public-linear')


class Gesture:
    def __init__(self, **kwargs): self.kwargs, self.raw_input = kwargs, None


class SceneView:
    def __init__(self): self.scene = Frame()


class Matrix(list):
    def __init__(self): super().__init__([[float(i == j) for j in range(4)] for i in range(4)])
    def TransformDir(self, vec): return vec


class Api:
    def __init__(self):
        self.id, self.usd_context_name, self.render_product_path = 'viewport', '', '/Render/Product'
        self.camera_path, self.stage = '/World/RenderCamera', object()
        self.time = NS(IsDefault=lambda: True, GetValue=lambda: 0)
        self.transform, self.view, self.projection = Matrix(), Matrix(), Matrix()
        self.attached, self.removed = [], []
    def add_scene_view(self, value): self.attached.append(value)
    def remove_scene_view(self, value): self.removed.append(value)


class Window:
    def __init__(self, api, app):
        self.viewport_widget = NS(viewport_api=api, display_delegate=object())
        self.frame, self.app_window, self.owned = Frame(), app, Frame()
        self.names = []
    def get_frame(self, name): self.names.append(name); return self.owned


def rig():
    api, app = Api(), object()
    window = Window(api, app)
    state = NS(api=api, window=window, app=app, scene=dict(projectUid='project',
        projectContentSha256='a' * 64, revision=7), epoch=3)
    sdk = NS(ui=NS(ZStack=Frame), sc=NS(SceneView=SceneView, DragGesture=Gesture,
        Curve=Curve, PolygonMesh=Shape), paired=lambda: (state.api, state.window),
        app=lambda: state.app, same_api=lambda a, b: a is b,
        getters={k: (lambda value: value is not None) for k in ('supports_viewport', 'supports_window',
            'supports_appwindow', 'is_app_window_attached')}, Gf=NS(Vec3d=lambda *x: x))
    events = []
    controller = p.PreviewController(lambda: (state.scene, state.epoch), events.append)
    grant = dict({k: binding()[k] for k in p.BASE_KEYS}, kind='rendererResult')
    controller.bind_committed(grant, state.epoch)
    producers = []
    factory = v.NativePreviewFactory(sdk)
    def install(handles, publish, current):
        producer = factory(handles, publish, current)
        producers.append(producer)
        return producer
    return NS(state=state, sdk=sdk, controller=controller, grant=grant, events=events,
        factory=install, producers=producers, api=api, window=window)


def raw(x=0, y=0):
    return NS(mouse_origin=(x, y, 10), mouse_direction=(0, 0, -1))


def drag(producer, kind, start, end):
    gesture = producer.gestures[kind]
    gesture.raw_input = start; gesture.on_began()
    gesture.raw_input = end; gesture.on_changed()
    assert producer.drag is not None
    gesture.on_ended()


def test_actual_controller_installs_owned_public_scene_view_and_end_readbacks():
    r = rig(); value = packet(); saved = copy.deepcopy(value)
    ack = r.controller.install(value, r.factory)
    assert ack['packet'] == saved and ack['ready'] is True and len(ack['leaseKey']) == 32
    producer = r.producers[0]
    assert r.api.attached == [producer.scene_view]
    assert r.window.names == ['dcdtuni.shared-postprocess-previews']
    assert len(producer.shapes['ring'].positions) == 65
    assert len(producer.shapes['seed'].positions) == 114
    assert len(producer.shapes['seed'].args[1]) == 224
    drag(producer, 'ring', raw(), raw(2, 3))
    assert r.events[0]['position'] == [2, 0, 0] and r.events[0]['setId'] == 42
    drag(producer, 'seed', raw(), raw(2, 3))
    assert r.events[1]['position'] == [2, 3, 0] and r.events[1]['setId'] == 9
    assert [e['sequence'] for e in r.events] == [1, 2]
    assert all(e['leaseKey'] == ack['leaseKey'] and e['binding'] == binding() for e in r.events)
    assert value == saved and r.grant['packetSha256'] == 'e' * 64


def test_changed_callback_does_not_publish_until_end():
    r = rig(); r.controller.install(packet(), r.factory); producer = r.producers[0]
    gesture = producer.gestures['seed']; gesture.raw_input = raw(); gesture.on_began()
    gesture.raw_input = raw(1, 2); gesture.on_changed()
    assert not r.events and producer.shapes['seed'].positions != v.sphere_geometry(packet()['handles'][1])[0]
    gesture.on_ended(); assert r.events[0]['position'] == [1, 2, 0]


@pytest.mark.parametrize('kind', ['ring', 'seed'])
def test_camera_change_mid_drag_cancels_and_restores_but_new_drag_can_begin(kind):
    r = rig(); r.controller.install(packet(), r.factory); producer = r.producers[0]
    gesture = producer.gestures[kind]; original = copy.deepcopy(producer.shapes[kind].positions)
    gesture.raw_input = raw(); gesture.on_began(); gesture.raw_input = raw(1, 2); gesture.on_changed()
    r.api.view[0][3] = .25; gesture.on_ended()
    assert not r.events and producer.drag is None and producer.shapes[kind].positions == original
    drag(producer, kind, raw(), raw(1, 2)); assert len(r.events) == 1


@pytest.mark.parametrize('change', ['api', 'window', 'app', 'stage', 'widget', 'delegate', 'camera', 'time', 'scene', 'epoch'])
def test_final_owner_camera_time_scene_gates_refuse_end(change):
    r = rig(); r.controller.install(packet(), r.factory); producer = r.producers[0]
    g = producer.gestures['seed']; g.raw_input = raw(); g.on_began()
    if change == 'api': r.state.api = Api()
    elif change == 'window': r.state.window = Window(r.api, r.state.app)
    elif change == 'app': r.state.app = object()
    elif change == 'stage': r.api.stage = object()
    elif change == 'widget': r.window.viewport_widget = NS(viewport_api=r.api, display_delegate=object())
    elif change == 'delegate': r.window.viewport_widget.display_delegate = object()
    elif change == 'camera': r.api.camera_path = '/Other/Camera'
    elif change == 'time': r.api.time = NS(IsDefault=lambda: False, GetValue=lambda: .015)
    elif change == 'scene': r.state.scene['revision'] += 1
    else: r.state.epoch += 1
    g.raw_input = raw(1, 2); g.on_ended()
    assert not r.events and producer.drag is None


def test_parallel_and_backward_pointer_rays_do_not_emit():
    r = rig(); r.controller.install(packet(), r.factory); producer = r.producers[0]
    g = producer.gestures['seed']
    for direction in [(1, 0, 0), (0, 0, 1)]:
        g.raw_input = NS(mouse_origin=(0, 0, 10), mouse_direction=direction)
        g.on_began(); g.on_ended()
        assert producer.drag is None and not r.events


def test_clear_retires_drag_native_shapes_and_pending_callbacks():
    r = rig(); r.controller.install(packet(), r.factory); producer = r.producers[0]
    g = producer.gestures['seed']; g.raw_input = raw(); g.on_began()
    r.controller.invalidate(); g.raw_input = raw(1, 2); g.on_ended()
    assert not r.events and producer.closed and producer.drag is None
    assert len(r.api.removed) == 1 and r.window.owned.clears == 1
    with pytest.raises(ValueError, match='No committed'): r.controller.install(packet(), r.factory)


def test_retire_before_delayed_install_stays_rejected_after_same_numeric_replay():
    r = rig(); r.controller.retire(binding()); r.controller.bind_committed(r.grant, r.state.epoch)
    with pytest.raises(ValueError, match='owner retired'): r.controller.install(packet(), r.factory)
    assert not r.producers


def test_late_old_adapter_retirement_does_not_clear_new_owner():
    r = rig(); r.controller.install(packet(), r.factory)
    value = packet(); value['binding']['ownerKey'] = '1' * 32
    ack = r.controller.install(value, r.factory)
    assert r.producers[0].closed and not r.producers[1].closed
    r.controller.retire(binding())
    drag(r.producers[1], 'seed', raw(), raw(2, 1))
    assert r.events[0]['leaseKey'] == ack['leaseKey'] and r.events[0]['binding']['ownerKey'] == '1' * 32


@pytest.mark.parametrize('key', list(p.BASE_KEYS))
def test_every_signed_numeric_commit_field_is_required_exact(key):
    r = rig(); value = packet()
    if key == 'source': value['binding']['source']['run']['receiptSha256'] = '1' * 64
    elif key == 'packetSha256': value['binding'][key] = '1' * 64
    else: value['binding'][key] += 1
    with pytest.raises(ValueError, match='committed source'): r.controller.install(value, r.factory)
    assert not r.producers


def test_mesh_commit_cannot_authorize_postprocess_previews():
    r = rig(); r.controller.bind_committed(dict(r.grant, kind='rendererMesh'), r.state.epoch)
    with pytest.raises(ValueError, match='No committed'): r.controller.install(packet(), r.factory)


@pytest.mark.parametrize('mutate', [lambda x: x.update(extra=True),
    lambda x: x['binding'].update(extra=True), lambda x: x['handles'].append(x['handles'][0]),
    lambda x: x['handles'][0].update(normal=[0, 0, 0]), lambda x: x['handles'][1].update(radius=0),
    lambda x: x['handles'][0].update(setId=True), lambda x: x['handles'][1].update(centre=[float('nan'), 0, 0]),
    lambda x: x['binding']['source'].update(run=None), lambda x: x['binding'].update(displayGeneration=1.5)])
def test_strict_packet_rejection_precedes_sdk_calls(mutate):
    r = rig(); value = packet(); mutate(value)
    with pytest.raises((ValueError, TypeError)): r.controller.install(value, r.factory)
    assert not r.producers


def test_nonunit_oblique_ring_and_sphere_are_actual_world_geometry():
    h = dict(kind='ring', origin=[1, 2, 3], normal=[3, 4, 5], radius=2)
    points = v.ring_positions(h); n = p.unit(h['normal'])
    for point in points:
        delta = [a-b for a,b in zip(point, h['origin'])]
        assert math.hypot(*delta) == pytest.approx(2)
        assert sum(a*b for a,b in zip(delta,n)) == pytest.approx(0, abs=1e-14)
    assert points[0] == pytest.approx(points[-1])
    positions, counts, indices = v.sphere_geometry(dict(centre=[3,4,5], radius=2))
    assert all(math.dist(point,[3,4,5]) == pytest.approx(2) for point in positions)
    assert set(counts) == {3} and min(indices) == 0 and max(indices) == len(positions)-1


def test_bridge_product_routes_and_committed_binding_are_not_test_only():
    path = EXT/'dc/cfd_bridge/extension.py'; source = path.read_text(); tree = ast.parse(source)
    assert 'setCommonPostprocessPreviews' in source and 'retireCommonPostprocessPreviews' in source
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_load_verified_result')
    calls = [n.func.attr for n in ast.walk(method) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert 'bind_committed' in calls
    route = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_route_render_command')
    names = {n.func.attr for n in ast.walk(route) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert {'install', 'retire', 'invalidate'} <= names
    assert 'NativePreviewFactory()' in ast.get_source_segment(source, route)
    assert 'postprocessPreviewEvent' in source and '"omni.ui.scene" = {}' in (EXT/'config/extension.toml').read_text()
    assert not any(n in (EXT/'dc/cfd_bridge/postprocess_preview_viewport.py').read_text()
        for n in ('set_slice(', 'update_slice(', 'create_streamlines(', 'capture_current('))


def test_real_bridge_router_installs_acks_retires_and_refuses_late_owner(monkeypatch, tmp_path):
    # Actual extension routing under existing controlled Kit harness, plus the
    # real preview controller and producer with the explicit public-SDK shim.
    import asyncio
    import importlib
    import json
    from test_dataset_scene_race import _harness
    async def scenario():
        ext, _, stage, _, _, _ = _harness(monkeypatch, tmp_path, None)
        r = rig(); r.api.stage = stage
        native = importlib.import_module('race_bridge.postprocess_preview_viewport')
        monkeypatch.setattr(native, '_sdk', lambda: r.sdk)
        value = packet(); value['binding']['source']['projectUid'] = 'old-project'
        value['binding']['sceneRevision'] = 1
        c = p.PreviewController(lambda: (ext._result_scene_identity(stage), ext._display_generation), r.events.append)
        c.bind_committed(dict({k: value['binding'][k] for k in p.BASE_KEYS}, kind='rendererResult'), ext._display_generation)
        ext._postprocess_preview_controller = c
        ext._route_render_command('setCommonPostprocessPreviews', json.dumps(dict(requestId='preview', packet=value)))
        first = ext.acks[-1]
        assert first['ok'] is True and first['postprocessPreviews']['packet'] == value
        owned = c._active['producer']
        drag(owned, 'seed', raw(), raw(1, 2))
        assert r.events[0]['binding'] == value['binding']
        ext._route_render_command('retireCommonPostprocessPreviews', json.dumps(dict(requestId='retire', binding=value['binding'])))
        assert ext.acks[-1]['ok'] is True and ext.acks[-1]['retiredPostprocessPreviews'] is True and owned.closed
        ext._route_render_command('setCommonPostprocessPreviews', json.dumps(dict(requestId='late', packet=value)))
        assert ext.acks[-1]['ok'] is False and 'owner retired' in ext.acks[-1]['error']
        ext._route_render_command('setCommonPostprocessPreviews', json.dumps(dict(requestId='extra', packet=value, sourcePick=True)))
        assert ext.acks[-1]['ok'] is False and 'Unexpected' in ext.acks[-1]['error']
    asyncio.run(scenario())


def test_destroyed_native_api_does_not_interrupt_owned_widget_and_controller_retirement():
    r = rig(); r.controller.install(packet(), r.factory); producer = r.producers[0]
    def destroyed(_): raise ReferenceError('controlled destroyed native API')
    r.api.remove_scene_view = destroyed
    r.controller.close()
    assert producer.closed and producer.scene_view is None and producer.frame is None
    assert r.window.owned.clears == 1 and r.controller._committed is None and r.controller._closed


def test_empty_handles_ack_is_exact_but_cannot_emit_an_interaction():
    r = rig(); value = packet(); value['handles'] = []
    ack = r.controller.install(value, r.factory)
    assert ack['packet'] == value and ack['ready'] is True and not r.producers
    assert not r.controller._emit(r.controller._active, 'seed', 9, (1,2,3)) and not r.events


@pytest.mark.parametrize('kind', ['ring', 'seed'])
def test_public_cancel_callback_restores_retires_drag_and_late_end_then_reinstall(kind):
    r = rig(); value = packet(); saved = copy.deepcopy(value); grant = copy.deepcopy(r.grant)
    r.controller.install(value, r.factory); producer = r.producers[0]
    gesture = producer.gestures[kind]; original = copy.deepcopy(producer.shapes[kind].positions)
    gesture.raw_input = raw(); gesture.on_began()
    gesture.raw_input = raw(1, 2); gesture.on_changed()
    assert producer.drag is not None and producer.shapes[kind].positions != original
    # Invoke the pinned SDK's public cancellation callback, not a private helper.
    gesture.on_canceled()
    assert producer.drag is None and producer.shapes[kind].positions == original and not r.events
    gesture.on_canceled(); gesture.on_ended()
    assert producer.drag is None and producer.shapes[kind].positions == original and not r.events
    assert value == saved and r.grant == grant and producer.ready()
    r.controller.install(value, r.factory)
    assert producer.closed and len(r.producers) == 2
    drag(r.producers[1], kind, raw(), raw(2, 3))
    assert len(r.events) == 1 and r.events[0]['setId'] == (42 if kind == 'ring' else 9)
    assert r.events[0]['position'] == ([2, 0, 0] if kind == 'ring' else [2, 3, 0])
    assert value == saved and r.grant == grant
