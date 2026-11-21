"""Owned public SceneView carrier for the common slice ring and seed sphere.

SDK construction/callability is separate from encoded-video pixel verification.
No encoder crop/scale transform is asserted here; output_layout stays fail closed.
"""
import copy
import math
from types import SimpleNamespace
from .common_postprocess_previews import move_handle, ray_plane, unit, vector


def _sdk():
    import omni.ui as ui
    from omni.ui import scene as sc
    from omni import appwindow
    from omni.kit.viewport.utility import get_active_viewport_and_window
    from pxr import Gf
    from .render_output_observations import _same_public_api, reviewed_runtime_getters
    return SimpleNamespace(ui=ui, sc=sc, paired=get_active_viewport_and_window,
        app=appwindow.get_default_app_window, same_api=_same_public_api,
        getters=reviewed_runtime_getters(), Gf=Gf)


def _matrix(value):
    result = tuple(float(value[row][column]) for row in range(4) for column in range(4))
    if not all(math.isfinite(v) for v in result):
        raise ValueError('Invalid native preview camera matrix')
    return result


def _time(value):
    if value.IsDefault():
        return ('default',)
    result = float(value.GetValue())
    if not math.isfinite(result):
        raise ValueError('Invalid native preview time')
    return ('time', result)


def ring_positions(handle):
    n = unit(handle['normal'])
    helper = (1, 0, 0) if abs(n[0]) < .8 else (0, 1, 0)
    u = unit((n[1] * helper[2] - n[2] * helper[1],
              n[2] * helper[0] - n[0] * helper[2], n[0] * helper[1] - n[1] * helper[0]))
    v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0])
    origin, radius = handle['origin'], handle['radius']
    return [list(vector(tuple(origin[j] + radius * (math.cos(i * math.tau / 64) * u[j]
        + math.sin(i * math.tau / 64) * v[j]) for j in range(3)))) for i in range(65)]


def sphere_geometry(handle):
    # Public PolygonMesh, since this pinned SDK has no Sphere shape.
    centre, radius = handle['centre'], handle['radius']
    positions = [[centre[0], centre[1] + radius, centre[2]]]
    for lat in range(1, 8):
        theta = math.pi * lat / 8
        for lon in range(16):
            phi = math.tau * lon / 16
            positions.append([centre[0] + radius * math.sin(theta) * math.cos(phi),
                centre[1] + radius * math.cos(theta), centre[2] + radius * math.sin(theta) * math.sin(phi)])
    south = len(positions)
    positions.append([centre[0], centre[1] - radius, centre[2]])
    indices = []
    for lon in range(16):
        following = (lon + 1) % 16
        indices.extend((0, 1 + following, 1 + lon))
        for lat in range(6):
            a, b = 1 + lat * 16 + lon, 1 + lat * 16 + following
            indices.extend((a, b, a + 16, b, b + 16, a + 16))
        indices.extend((south, 1 + 6 * 16 + lon, 1 + 6 * 16 + following))
    for position in positions:
        vector(position)
    return positions, [3] * (len(indices) // 3), indices


class NativePreviewFactory:
    """SDK injection is only for explicitly labelled CPU API-contract controls."""
    def __init__(self, sdk=None):
        self.sdk = sdk if sdk is not None else _sdk()

    def __call__(self, handles, publish, require_current):
        return NativePreviewProducer(self.sdk, handles, publish, require_current)


class NativePreviewProducer:
    def __init__(self, sdk, handles, publish, require_current):
        self.sdk, self.publish, self.require_current = sdk, publish, require_current
        self.closed, self.drag = False, None
        self.scene_view = self.frame = None
        self.handles = copy.deepcopy(handles)
        self.shapes, self.gestures = {}, {}
        self.api, self.window = sdk.paired()
        self.app = sdk.app()
        getters = sdk.getters
        if (getters is None or not getters['supports_viewport'](self.api)
                or not getters['supports_window'](self.window) or not getters['supports_appwindow'](self.app)
                or not getters['is_app_window_attached'](self.app)):
            raise ValueError('Native preview public runtime unsupported')
        self.widget, self.window_frame = self.window.viewport_widget, self.window.frame
        self.delegate = self.widget.display_delegate
        self.stage = self.api.stage
        self.native_identity = self._sample_native_identity()
        if self.stage is None or str(self.api.camera_path) != '/World/RenderCamera':
            raise ValueError('Native preview camera/stage unavailable')
        self.require_current()
        try:
            self.frame = self.window.get_frame('dcdtuni.shared-postprocess-previews')
            with self.frame:
                with sdk.ui.ZStack():
                    self.scene_view = sdk.sc.SceneView()
                    with self.scene_view.scene:
                        for handle in self.handles:
                            gesture = self._make_gesture(handle)
                            if handle['kind'] == 'ring':
                                positions = ring_positions(handle)
                                shape = sdk.sc.Curve(positions, colors=[(1, .65, .1, 1)] * len(positions),
                                    curve_type=sdk.sc.Curve.CurveType.LINEAR, thickness=3,
                                    intersection_thickness=10, gestures=[gesture])
                            else:
                                positions, counts, indices = sphere_geometry(handle)
                                shape = sdk.sc.PolygonMesh(positions, [(0, .75, 1, .75)] * len(positions),
                                    counts, indices, gestures=[gesture])
                            self.shapes[handle['kind']] = shape
                            self.gestures[handle['kind']] = gesture
            self.api.add_scene_view(self.scene_view)
            if not self.ready():
                raise ValueError('Native preview viewport ownership changed during construction')
        except Exception:
            self.close()
            raise

    def _sample_native_identity(self):
        api = self.api
        return (str(api.id), str(api.usd_context_name), str(api.render_product_path),
                str(api.camera_path), _time(api.time))

    def ready(self):
        if self.closed:
            return False
        try:
            api, window = self.sdk.paired()
            self.require_current()
            return (window is self.window and self.sdk.same_api(api, self.api)
                and self.sdk.same_api(self.widget.viewport_api, self.api)
                and self.window.viewport_widget is self.widget and self.window.frame is self.window_frame
                and self.widget.display_delegate is self.delegate and self.window.app_window is self.app
                and self.sdk.app() is self.app and self.api.stage is self.stage
                and self._sample_native_identity() == self.native_identity)
        except (ValueError, AttributeError, ReferenceError, TypeError, RuntimeError):
            return False

    def _camera(self):
        if not self.ready():
            raise ValueError('Native preview owner retired')
        transform = self.api.transform
        direction = unit(tuple(transform.TransformDir(self.sdk.Gf.Vec3d(0, 0, -1))))
        return (_matrix(self.api.view), _matrix(self.api.projection), _matrix(transform), direction)

    def _make_gesture(self, handle):
        producer = self
        class PreviewDrag(self.sdk.sc.DragGesture):
            def on_began(self):
                producer._began(handle, self.raw_input)
            def on_changed(self):
                producer._changed(handle, self.raw_input, ended=False)
            def on_ended(self):
                producer._changed(handle, self.raw_input, ended=True)
        return PreviewDrag(mouse_button=0, check_mouse_moved=True)

    def _began(self, handle, raw):
        self._cancel_drag()
        try:
            camera = self._camera()
            centre = handle['origin'] if handle['kind'] == 'ring' else handle['centre']
            hit = ray_plane(tuple(raw.mouse_origin), tuple(raw.mouse_direction), centre, camera[3])
            self.drag = dict(handle=handle, camera=camera, start_hit=hit, position=tuple(centre))
        except (ValueError, AttributeError, ReferenceError, TypeError, RuntimeError):
            self.drag = None

    def _positions(self, handle, position):
        value = dict(handle)
        value['origin' if handle['kind'] == 'ring' else 'centre'] = list(position)
        return ring_positions(value) if handle['kind'] == 'ring' else sphere_geometry(value)[0]

    def _cancel_drag(self):
        drag, self.drag = self.drag, None
        if drag and not self.closed:
            handle = drag['handle']
            centre = handle['origin'] if handle['kind'] == 'ring' else handle['centre']
            self.shapes[handle['kind']].positions = self._positions(handle, centre)

    def _changed(self, handle, raw, ended):
        drag = self.drag
        if drag is None or drag['handle'] is not handle:
            return
        try:
            if self._camera() != drag['camera']:
                raise ValueError('Native preview camera changed during drag')
            centre = handle['origin'] if handle['kind'] == 'ring' else handle['centre']
            hit = ray_plane(tuple(raw.mouse_origin), tuple(raw.mouse_direction), centre, drag['camera'][3])
            position = move_handle(handle, drag['start_hit'], hit)
            # Re-check every final native getter after calculating the endpoint.
            if self._camera() != drag['camera']:
                raise ValueError('Native preview camera changed during readback')
            self.shapes[handle['kind']].positions = self._positions(handle, position)
            drag['position'] = position
            if ended:
                if not self.publish(handle['kind'], handle['setId'], position):
                    self._cancel_drag()
                    return
                handle['origin' if handle['kind'] == 'ring' else 'centre'] = list(position)
                self.drag = None
        except (ValueError, AttributeError, ReferenceError, TypeError, RuntimeError):
            self._cancel_drag()

    def close(self):
        if self.closed:
            return
        self.drag = None
        self.closed = True
        scene_view, self.scene_view = self.scene_view, None
        frame, self.frame = self.frame, None
        if scene_view is not None:
            # Kit can destroy the viewport before extension shutdown; always
            # release our remaining owned widgets even if its API is gone.
            for cleanup in (lambda: self.api.remove_scene_view(scene_view), scene_view.scene.clear):
                try:
                    cleanup()
                except (ReferenceError, AttributeError, RuntimeError):
                    pass
        if frame is not None:
            try:
                frame.clear()
            except (ReferenceError, AttributeError, RuntimeError):
                pass
        self.shapes.clear()
        self.gestures.clear()
