"""Default-off readonly renderer metadata. Never a layout/stream-frame proof.

Only this reviewed installed cohort is supported. A module-path/source gate is
not proof that a running process's in-memory module equals later disk bytes.
No pixels, textures, private fields, subscriptions, settings or USD writes.
"""
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import weakref

OBSERVATION_BYTES = 6144
MAX_SAFE = 9007199254740991
SCHEMA = 'dcdtuni.render-output-observations/v1'
REASONS = ('none', 'unsupported-runtime', 'no-viewport', 'owner-mismatch',
           'changed-during-observation', 'missing-public-value', 'invalid-public-value',
           'headless-or-unattached', 'observation-limit')
REVIEWED_SOURCES = (('omni.kit.widget.viewport.api', 'omni.kit.widget.viewport-e33603573147cc56', (('omni/kit/widget/viewport/api.py', '0f2ed3f0b60162aca96ab7d0bac983f4735a88e786acfd6c77ce0c348d01a22c'), ('omni/kit/widget/viewport/widget.py', '05b000e92fec9e624844196260c7404de6a3e5589a9db524291ac8b5814d2170'), ('omni/kit/widget/viewport/capture.py', 'fe8944c6ada65abe09996f63b7b834f4efd82be1add6a4a9d7d1998dea3a1edf'), ('omni/kit/widget/viewport/impl/texture.py', 'e803cd662fa7c163d1dfb7bb59ee7bce99656b81099639aa7170664144de3f80'), ('config/extension.toml', 'a68a597b8799f323129673a51c4e07df219547d2c2b53e72edc063e5d1dbf356'))), ('omni.kit.viewport.window.window', 'omni.kit.viewport.window-6b8bd362e4a950d0', (('omni/kit/viewport/window/window.py', 'ca8b008d92ec7524080a38e3dbb40b5db837d7beabce2aaa667f0129b4a7d5b4'), ('omni/kit/viewport/window/layers.py', '3d6598eaaa011eb416248b8ed85f7d5daec748d8636c997154be0c6022777cd2'), ('config/extension.toml', 'bd512dc7ee53bcfdb9ba546c3fe5915b9007b687250dc89efb0e427c520c657a'))), ('omni.hydratexture', 'omni.kit.hydra_texture-1e941753365238e1', (('omni/hydratexture/_hydra_texture.pyi', '002db0f9d5264512639f685bc90bf152f7d416b5394ba3d8d1cc349d842c5223'), ('config/extension.toml', '19be90e085daf387e2a483da2204043125230013b1fcb44d424a5091d03fb268'), ('omni/hydratexture/__init__.py', '10a2322e922e431fb1a28f43c1bc1994da67ee2db4bc20df957e59e25cf6a46a'))), ('omni.appwindow', 'omni.appwindow-35766b062bc43ad5', (('omni/appwindow/_appwindow.pyi', '01de7bf4bfbdbe39e6f841d239e1fccd96749905020a8f700bfe89e065f70686'), ('config/extension.toml', 'c7d4b0db28c43a22ea053ccb53f137a3f48c7300a63151a4402f6cb942b4347e'), ('bin/libomni.appwindow.plugin.so', '338de3bbe317b97d81e18d3ca2e44ede75174136f3cf8d7b912363de581cab75'), ('omni/appwindow/__init__.py', 'c7f121ca098692dfe25e4db1d63294c81acc55cceceb3c05585a62683d73821d'))), ('omni.kit.renderer.bind', 'omni.kit.renderer.core-b0c65c993703d4a5', (('config/extension.toml', 'd3b2a9dc78bb08646c809446a78b339f427bb37c06a101e0467871c4706ae87e'), ('omni/kit/renderer/bind/_renderer.pyi', 'a5b59db2e38ef3e208e90e949100411c68d7c6d9fcab64bb30313cc3b9c4f93e'), ('omni/kit/renderer/bind/__init__.py', '42c3519a1853586bc04bbf02df9c68c43819d00089bb929c77800f04e95234f6'), ('bin/libomni.kit.renderer.plugin.so', '2baf35c3438bd8f46e75b38680c94ccd669c14fa6a494c13fb53333b5e1f5367'), ('bin/libomni.kit.renderer.ext.plugin.so', 'fb66916b239f9d2faba48979800786caf0c549018c57c38dd7d743a2f2fc7afc'))), ('omni.ui', 'omni.ui-0e529a493c7b9bf0', (('omni/ui/_ui.pyi', 'dae2fa71034bc4ab4aca979d6c74fbe8f2ab060ad95267e06322bfa054b69902'),)))


def _unavailable(reason):
    return {'status': 'unavailable', 'reason': reason}


def _refusal(reason):
    return dict(_unavailable(reason), schema=SCHEMA, layoutProof='unresolved',
                frameSourceBindingProof='unresolved')


def _number(v):
    if type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e18:
        raise ValueError('invalid-public-value')
    return v


def _ordinal(v):
    if type(v) is not int or not 0 <= v <= MAX_SAFE:
        raise ValueError('invalid-public-value')
    return v


def _dimensions(v):
    a = list(v)
    if len(a) != 2 or any(type(x) is not int or not 0 < x <= 16384 for x in a) or a[0] * a[1] > 134217728:
        raise ValueError('invalid-public-value')
    return a


def _matrix(v):
    # Hydra frame_info is a flat 16-list; public Gf matrices have 4 rows.
    a = list(v)
    if len(a) == 4:
        a = [x for row in a for x in list(row)]
    if len(a) != 16:
        raise ValueError('invalid-public-value')
    return [_number(x) for x in a]


def _string(v, cap, empty=False):
    if type(v) is not str or (not v and not empty) or len(v.encode('utf-8')) > cap or re.search(r'[\x00-\x1f\x7f]', v):
        raise ValueError('invalid-public-value')
    return v


def _observed(fn):
    try:
        return dict(status='observed', reason='none', **fn())
    except (AttributeError, KeyError):
        return _unavailable('missing-public-value')
    except ValueError as exc:
        return _unavailable(str(exc) if str(exc) in REASONS else 'invalid-public-value')
    except Exception:
        return _unavailable('invalid-public-value')  # no native exception/payload serialization


def reviewed_runtime_getters():
    """Version/source gate on public module entries; no vendor private access.

    Unknown module roots or changed source refuse before any renderer getter.
    Only hashes/whitelisted relative files are read. No user config or assets.
    """
    try:
        modules = {}
        for name, basename, files in REVIEWED_SOURCES:
            module = importlib.import_module(name)
            path = Path(module.__file__).resolve()
            root = next((p for p in (path.parent, *path.parents) if p.name == basename), None)
            if root is None:
                return None
            for rel, expected in files:
                target = root / rel
                if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                    return None
            modules[name] = module
        renderer = modules['omni.kit.renderer.bind'].get_renderer_interface()
        result = {key: getattr(renderer, key) for key in
                  ('is_app_window_attached', 'get_framebuffer_width', 'get_framebuffer_height')}
        if not all(callable(v) for v in result.values()):
            return None
        # The native utility's current wrapper must belong to the reviewed
        # loaded public classes too; disk/module path alone cannot bind an old
        # object retained across a module reload.
        viewport_type = modules['omni.kit.widget.viewport.api'].ViewportAPI
        window_type = modules['omni.kit.viewport.window.window'].ViewportWindow
        app_type = modules['omni.appwindow'].IAppWindow
        if not all(isinstance(t, type) for t in (viewport_type, window_type, app_type)):
            return None
        result['supports_viewport'] = lambda api: api.__class__ is viewport_type
        result['supports_window'] = lambda window: window.__class__ is window_type
        result['supports_appwindow'] = lambda app: app.__class__ is app_type
        return result
    except Exception:
        return None


def _frame(api):
    info = api.frame_info
    if type(info) is not dict or not info:
        raise AttributeError('missing frame')
    return {'frameNumber': _ordinal(info['frame_number']),
            'resolution': _dimensions(info['resolution']),
            'view': _matrix(info['view']), 'projection': _matrix(info['projection'])}


def _same_public_api(a, b):
    # Python standard weakref identity, never generic equality or repr/private
    # unwrapping. The public utility normally returns the stored widget proxy.
    if a is b:
        return True
    for owner, reference in ((a, b), (b, a)):
        if type(owner) in weakref.ProxyTypes:
            continue
        try:
            if any(ref is reference for ref in weakref.getweakrefs(owner)):
                return True
        except TypeError:
            pass
    return False


def _owner(api, window, stage, expected_camera_path):
    if api is None or window is None or api.stage is not stage:
        raise ValueError('owner-mismatch')
    camera = _string(str(api.camera_path), 512)
    if camera != expected_camera_path:
        raise ValueError('owner-mismatch')
    widget = window.viewport_widget
    paired = widget.viewport_api
    # Public wrappers expose weakref.proxy; compare their public native id.
    if (not _same_public_api(paired, api) or paired.stage is not stage or paired.id != api.id
            or paired.usd_context_name != api.usd_context_name or str(paired.camera_path) != camera
            or paired.render_product_path != api.render_product_path or paired.time != api.time):
        raise ValueError('owner-mismatch')
    owner = {'viewportId': _string(api.id, 128), 'usdContextName': _string(api.usd_context_name, 128, True),
             'cameraPath': camera, 'renderProductPath': _string(api.render_product_path, 512)}
    return owner, widget


def _context(c):
    if type(c) is not dict or c.get('stage') is None:
        raise ValueError('owner-mismatch')
    _ordinal(c['sceneRevision']); _ordinal(c['displayGeneration'])
    h = c['projectHash']
    if type(h) is not str or not re.fullmatch(r'[0-9a-f]{64}', h):
        raise ValueError('owner-mismatch')
    return c['stage'], {'sceneRevision': c['sceneRevision'], 'projectHash': h,
                        'displayGeneration': c['displayGeneration']}


def _bounded(v):
    raw = json.dumps(v, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > OBSERVATION_BYTES:
        raise ValueError('observation-limit')
    return v


def sample_render_output_observations(api, viewport_window, app_window, renderer_getters,
                                      expected_camera_path, read_context, read_complete_view, paired_viewport):
    """Synchronous one-attempt observation; latest frame is NOT scene proof.

    `app_window` is an owned producer, not an incoming pointer or guessed size.
    Controlled CPU fixtures may pass controlled getters; wire cannot supply them.
    """
    if renderer_getters is None:
        return _refusal('unsupported-runtime')
    if api is None or viewport_window is None:
        return _refusal('no-viewport')
    try:
        if (renderer_getters['supports_viewport'](api) is not True or
                renderer_getters['supports_window'](viewport_window) is not True):
            return _refusal('unsupported-runtime')
        stage, context = _context(read_context())
        view = json.loads(json.dumps(read_complete_view(), allow_nan=False))
        owner, widget = _owner(api, viewport_window, stage, expected_camera_path)
        native_time = api.time
        initial_api, initial_window = paired_viewport()
        initial_owner, _ = _owner(initial_api, initial_window, stage, expected_camera_path)
        if (initial_owner != owner or initial_window is not viewport_window
                or not _same_public_api(initial_api, api)):
            return _refusal('owner-mismatch')
        app = app_window()
        if app is not None and renderer_getters['supports_appwindow'](app) is not True:
            return _refusal('unsupported-runtime')
        widget_ref = widget
        # Reviewed ViewportWidget.viewport_api returns its stored public proxy;
        # pin that handle, not `api is returnedSelf` across proxy/strong forms.
        widget_api_ref = widget.viewport_api
        frame_ref = viewport_window.frame
        delivered = _observed(lambda: _frame(api))
        canvas = _observed(lambda: {'view': _matrix(api.view), 'projection': _matrix(api.projection),
            'transform': _matrix(api.transform), 'resolution': _dimensions(api.resolution),
            'fullResolution': _dimensions(api.full_resolution), 'fillFrame': _bool(api.fill_frame)})
        rect = _observed(lambda: {'rectKind': 'outerViewportWindowFrame', 'positionUnit': 'screen-pixel',
            'dimensionUnit': 'native-raw-unresolved',
            'position': [_number(frame_ref.screen_position_x), _number(frame_ref.screen_position_y)],
            'size': _positive_pair(frame_ref.computed_width, frame_ref.computed_height),
            'contentSize': _positive_pair(frame_ref.computed_content_width, frame_ref.computed_content_height),
            'innerContentRect': None, 'innerContentProof': 'unresolved'})
        association = _observed(lambda: {'viewportId': _string(widget.viewport_api.id, 128),
            'name': _string(widget.name, 128, True), 'visible': _bool(widget.visible),
            'innerContentRect': None, 'innerContentProof': 'unresolved'})
        application = (_unavailable('headless-or-unattached') if app is None else
            _observed(lambda: _app_observation(app)))
        framebuffer = (_unavailable('headless-or-unattached') if app is None else
            _observed(lambda: _framebuffer(app, renderer_getters)))
        # Re-read all value groups as well as owner pointers. Resizing/mutating
        # a stable window or API must not produce a mixed-time observation.
        final_canvas = _observed(lambda: {'view': _matrix(api.view), 'projection': _matrix(api.projection),
            'transform': _matrix(api.transform), 'resolution': _dimensions(api.resolution),
            'fullResolution': _dimensions(api.full_resolution), 'fillFrame': _bool(api.fill_frame)})
        final_rect = _observed(lambda: {'rectKind': 'outerViewportWindowFrame', 'positionUnit': 'screen-pixel',
            'dimensionUnit': 'native-raw-unresolved',
            'position': [_number(frame_ref.screen_position_x), _number(frame_ref.screen_position_y)],
            'size': _positive_pair(frame_ref.computed_width, frame_ref.computed_height),
            'contentSize': _positive_pair(frame_ref.computed_content_width, frame_ref.computed_content_height),
            'innerContentRect': None, 'innerContentProof': 'unresolved'})
        final_association = _observed(lambda: {'viewportId': _string(widget.viewport_api.id, 128),
            'name': _string(widget.name, 128, True), 'visible': _bool(widget.visible),
            'innerContentRect': None, 'innerContentProof': 'unresolved'})
        final_application = (_unavailable('headless-or-unattached') if app is None else
            _observed(lambda: _app_observation(app)))
        final_framebuffer = (_unavailable('headless-or-unattached') if app is None else
            _observed(lambda: _framebuffer(app, renderer_getters)))
        # Capture every last public identity getter before the closing native
        # metadata/source fence. The final conditional is a pure comparison;
        # no widget/window/API getter can run after that fence.
        final_owner, final_widget = _owner(api, viewport_window, stage, expected_camera_path)
        final_widget_api_ref = final_widget.viewport_api
        final_window_frame_ref = viewport_window.frame
        final_app = app_window()
        paired_api, paired_window = paired_viewport()
        paired_owner, _ = _owner(paired_api, paired_window, stage, expected_camera_path)
        same_paired_api = _same_public_api(paired_api, api)
        # Complete native values follow all potentially late widget/window/
        # active-owner getters. Read-only owned context/view callbacks close
        # after metadata, too, to catch side effects in the final API getter.
        final_frame = _observed(lambda: _frame(api))
        final_time = api.time
        final_view = read_complete_view()
        final_stage, final_context = _context(read_context())
        if (final_stage is not stage or final_context != context or final_view != view
                or final_owner != owner or paired_owner != owner or paired_window is not viewport_window
                or not same_paired_api or final_time != native_time or final_widget is not widget_ref
                or final_widget_api_ref is not widget_api_ref
                or final_window_frame_ref is not frame_ref or final_app is not app
                or final_frame != delivered or final_canvas != canvas or final_rect != rect
                or final_association != association or final_application != application
                or final_framebuffer != framebuffer):
            return _refusal('changed-during-observation')
        # An older frame may remain under a stable accepted scene. Explicitly
        # unresolved; no fabricated scene epoch or shared encoder frame identity.
        return _bounded({'schema': SCHEMA, 'status': 'observed', 'reason': 'none',
            'layoutProof': 'unresolved', 'frameSourceBindingProof': 'unresolved',
            'observationContext': context, 'viewportOwner': dict(status='observed', reason='none', **owner),
            'deliveredViewportFrame': delivered, 'viewportCanvas': canvas, 'viewportWindow': rect,
            'widgetAssociation': association, 'applicationWindow': application, 'framebuffer': framebuffer})
    except ValueError as exc:
        reason = str(exc)
        return _refusal(reason if reason in REASONS else 'invalid-public-value')
    except Exception:
        return _refusal('invalid-public-value')


def _bool(v):
    if type(v) is not bool:
        raise ValueError('invalid-public-value')
    return v


def _positive_pair(x, y):
    a = [_number(x), _number(y)]
    if any(v <= 0 for v in a):
        raise ValueError('invalid-public-value')
    return a


def _app_observation(app):
    size = app.get_size()
    dims = _dimensions((size.x, size.y))
    wh = _dimensions((app.get_width(), app.get_height()))
    if dims != wh:
        raise ValueError('invalid-public-value')
    dpi, ui = _positive_pair(app.get_dpi_scale(), app.get_ui_scale())
    return {'size': dims, 'dimensionUnit': 'native-raw-unresolved', 'dpiScale': dpi, 'uiScale': ui}


def _framebuffer(app, getters):
    attached = getters['is_app_window_attached'](app)
    if type(attached) is not bool:
        raise ValueError('invalid-public-value')
    if not attached:
        # Explicit status instead of treating a headless zero size as observed.
        raise ValueError('headless-or-unattached')
    return {'attached': True, 'size': _dimensions((getters['get_framebuffer_width'](app),
                                                  getters['get_framebuffer_height'](app))),
            'dimensionUnit': 'framebuffer-pixel'}
