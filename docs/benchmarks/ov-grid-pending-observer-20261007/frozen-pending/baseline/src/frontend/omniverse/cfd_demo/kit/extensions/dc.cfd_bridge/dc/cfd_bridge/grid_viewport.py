"""Public viewport observations for common decoration; no source/grant authority.

The default installed widget lacks a public link to its actual UI frame.
Its outer Window rectangle is an observation, not a texture-to-stream proof.
Until an owned display layout supplies that link, output lowering stays pending.
"""
from __future__ import annotations
import math


def _numbers(values, count):
    vals = list(values)
    if len(vals) != count or any(type(x) not in (int, float) or not math.isfinite(x) for x in vals):
        raise ValueError('non-finite public viewport observation')
    return [float(x) for x in vals]


def _size(values):
    vals = _numbers(values, 2)
    if any(x <= 0 or int(x) != x for x in vals):
        raise ValueError('public viewport raster is not ready')
    return [int(x) for x in vals]


def _matrix(matrix):
    return _numbers([matrix[i][j] for i in range(4) for j in range(4)], 16)


def _near(a, b):
    return all(abs(x-y) <= 1e-6 * max(1., abs(x), abs(y)) for x,y in zip(a,b))


def sample_grid_viewport(viewport_api, expected_stage, expected_camera_path,
                         current_scene_context=None, viewport_window=None, app_window=None,
                         output_layout=None):
    """Bounded readonly public API sample. No raw-camera fallback is returned.

    output_layout is reserved for an owned native display producer. It is not
    read from a browser request/snapshot. Current legacy layout supplies None.
    """
    from pxr import Gf, UsdGeom, CameraUtil
    from .output_layout import OwnedOutputLayout, same_layout_lease
    obs = {'layoutProof':'unresolved'}
    def pending(reason):
        return {'phase':'pending','reason':reason,'observations':obs,'projection':None}
    if viewport_api is None:
        return pending('viewport-unavailable')
    try:
        initial_layout_lease = None
        if output_layout is not None:
            if type(output_layout) is not OwnedOutputLayout:
                return pending('stream-layout-unresolved')
            initial_layout_lease = output_layout.current_lease()
        obs['stageSame'] = viewport_api.stage == expected_stage
        obs['cameraPath'] = str(viewport_api.camera_path)
        if not obs['stageSame'] or obs['cameraPath'] != str(expected_camera_path):
            return pending('viewport-scene-not-current')
        obs['resolution'] = _size(viewport_api.resolution)
        obs['fullResolution'] = _size(viewport_api.full_resolution)
        world = viewport_api.world_to_ndc
        obs['worldToNdc'] = _matrix(world)
        obs['view'] = _matrix(viewport_api.view)
        obs['transform'] = _matrix(viewport_api.transform)
        obs['uiProjection'] = _matrix(viewport_api.projection)
        samples = []
        # A returned None says a point lies outside the texture, not that the
        # finite mapping is absent. The public API is a weakref.proxy: do not
        # compare the returned internal self by identity against that proxy.
        for point in ((0.,0.),(1.,0.),(0.,1.),(-.5,.5),(.25,-.25)):
            uv, hit = viewport_api.map_ndc_to_texture(point)
            samples.append({'ndc':list(point),'uv':_numbers(uv,2),'insideTexture':hit is not None})
        obs['ndcToTextureSamples'] = samples
        o, x, y = [s['uv'] for s in samples[:3]]
        ax, ay = [x[i]-o[i] for i in range(2)], [y[i]-o[i] for i in range(2)]
        det = ax[0]*ay[1]-ax[1]*ay[0]
        if not math.isfinite(det) or abs(det) <= 1e-12:
            return pending('viewport-texture-mapping-degenerate')
        for sample in samples[3:]:
            u,v = sample['ndc']
            if not _near(sample['uv'],[o[i]+u*ax[i]+v*ay[i] for i in range(2)]):
                return pending('viewport-texture-mapping-not-affine')
        image_from_ui = Gf.Matrix4d(2*ax[0],2*ax[1],0,0, 2*ay[0],2*ay[1],0,0,
                                    0,0,1,0, 2*o[0]-1,2*o[1]-1,0,1)
        image_world = world * image_from_ui
        obs['imageWorldToNdc'] = _matrix(image_world)
        if app_window is not None:
            size = app_window.get_size()
            obs['appSizePx'] = _size((size.x,size.y))
            pos = app_window.get_position()
            obs['appPositionScreenPx'] = _numbers((pos.x,pos.y),2)
            # Separate observed quantities: do not silently DPI-scale positions.
            dpi, ui = _numbers((app_window.get_dpi_scale(),app_window.get_ui_scale()),2)
            if dpi <= 0 or ui <= 0:
                return pending('public-app-scale-unavailable')
            obs['appDpiScale'] = dpi
            obs['appUiScale'] = ui
        if viewport_window is not None:
            frame = viewport_window.frame
            obs['outerWindowRectObserved'] = _numbers((frame.screen_position_x,frame.screen_position_y,
                frame.computed_content_width,frame.computed_content_height),4)
            obs['viewportWidgetName'] = str(viewport_window.viewport_widget.name)[:128]
        camera = UsdGeom.Camera(expected_stage.GetPrimAtPath(expected_camera_path))
        if not camera:
            return pending('viewport-camera-unavailable')
        if not _near(obs['view'], _matrix(camera.ComputeLocalToWorldTransform(viewport_api.time).GetInverse())):
            return pending('viewport-camera-frame-not-current')
        # Installed API conforms using MatchHorizontally before its UI mapping.
        # Compare its DELIVERED image result to current authored inputs, rather
        # than substituting those inputs for an unavailable delivered matrix.
        expected = camera.GetCamera(viewport_api.time)
        width,height = obs['fullResolution']
        CameraUtil.ConformWindow(expected,CameraUtil.MatchHorizontally,width/height)
        expected_world = Gf.Matrix4d(*obs['view']) * expected.frustum.ComputeProjectionMatrix()
        if not _near(obs['imageWorldToNdc'],_matrix(expected_world)):
            return pending('viewport-projection-frame-not-current')
        obs['layoutProof'] = 'unresolved'
        # The legacy public widget exposes no Frame getter. Outer window/name
        # matching is not evidence for its private actual canvas. In particular
        # an unnamed widget cannot be located by guessing a same-sized child.
        if output_layout is None:
            return pending('stream-layout-unresolved')
        # A future owned producer must supply a native frame association and a
        # texture-to-whole-output transform. It cannot be a dict from the wire.
        if initial_layout_lease is None:
            return pending('stream-layout-unresolved')
        layout = output_layout.capture_current(viewport_api,viewport_window,app_window)
        if layout is None:
            return pending('stream-layout-unresolved')
        # Native producer cannot currently produce a transform. These closing
        # fences remain mandatory if a reviewed stream producer is later added.
        final_layout_lease = output_layout.current_lease()
        if (not same_layout_lease(initial_layout_lease, final_layout_lease)
                or viewport_api.stage != expected_stage
                or str(viewport_api.camera_path) != str(expected_camera_path)
                or _matrix(viewport_api.world_to_ndc) != obs['worldToNdc']
                or _matrix(viewport_api.view) != obs['view']
                or _matrix(viewport_api.transform) != obs['transform']
                or _matrix(viewport_api.projection) != obs['uiProjection']
                or _size(viewport_api.resolution) != obs['resolution']
                or _size(viewport_api.full_resolution) != obs['fullResolution']):
            return pending('stream-layout-unresolved')
        transform = Gf.Matrix4d(*_numbers(layout['imageNdcToOutputNdc'],16))
        if abs(transform.GetDeterminant()) <= 1e-12:
            return pending('stream-layout-degenerate')
        obs['layoutProof'] = 'owned-native-frame'
        obs['imageNdcToOutputNdc'] = _matrix(transform)
        return {'phase':'ready','reason':None,'observations':obs,
                'layoutLease':final_layout_lease, 'projection':_matrix(image_world*transform)}
    except Exception:
        # Fixed reason is safe for the UI; arbitrary native payload is not logged.
        return pending('public-viewport-observation-unavailable')


# Native texture geometry is a different domain from decoded output parity.
# These typed observations never cross the wire or supply a stream-frame grant.
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class NativeGridObservation:
    api: object
    window: object
    owner: tuple
    context: str
    view_token: str
    intent: str
    view_revision: int | None
    projection: tuple
    resolution: tuple
    full_resolution: tuple
    frame_number: int


def same_native_grid_observation(a, b):
    from .render_output_observations import _same_public_api
    return (type(a) is NativeGridObservation and type(b) is NativeGridObservation
        and _same_public_api(a.api, b.api) and a.window is b.window
        and a.owner == b.owner and a.context == b.context
        and a.view_token == b.view_token and a.intent == b.intent
        and a.view_revision == b.view_revision and a.projection == b.projection
        and a.resolution == b.resolution and a.full_resolution == b.full_resolution
        and b.frame_number >= a.frame_number)


# Fixed local opt-in: observations only, never frame/grant/pixel authority.
import os
_NATIVE_GRID_DIAGNOSTICS = os.environ.get('DC_CFD_NATIVE_GRID_DIAGNOSTICS') == 'grid-native-v1'
_NATIVE_GRID_DIAGNOSTICS_SEEN = set()
_NATIVE_GRID_DIAGNOSTICS_LIMIT = 8


# Separately opt-in trace; these values are never observation authority.
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
_NATIVE_GRID_COHORT_DIAGNOSTICS = os.environ.get('DC_CFD_NATIVE_GRID_COHORT_DIAGNOSTICS') == 'grid-cohort-v1'
_NATIVE_GRID_COHORT_LIMIT = 8
_NATIVE_GRID_COHORT_REQUEST = ContextVar('dc_native_grid_diagnostic_request', default=None)
_NATIVE_GRID_COHORT_SEEN = set()
_NATIVE_GRID_COHORT_LAST = {}


def _cohort_json(value):
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(text.encode('utf-8')) > 16384:
        raise ValueError('cohort diagnostic input bound')
    return text


@contextmanager
def native_grid_cohort_request(view, context, serial):
    """By-value request scope for logging only; no Kit reads or request changes."""
    token = None
    if _NATIVE_GRID_COHORT_DIAGNOSTICS:
        payload = None
        try:
            text = _cohort_json({k:view.get(k) for k in
                ('backgroundGrid','backgroundGridRaster','camera','timeSeconds')})
            payload = {'viewToken':text, 'contextToken':_cohort_json(context),
                       'requestSerial':serial}
        except Exception:
            pass
        # Invalid inner requests must hide an outer token instead of borrowing it.
        token = _NATIVE_GRID_COHORT_REQUEST.set(payload)
    try:
        yield
    finally:
        if token is not None:
            try:
                _NATIVE_GRID_COHORT_REQUEST.reset(token)
            except Exception:
                pass


def _native_grid_cohort_diagnostic(stage, owner, camera_path, expected_camera_path,
                                  context, accepted_view, frame, resolution, full,
                                  maps, view, image_projection, delivered_projection,
                                  image_world, delivered_world, camera_matches):
    """One packet from an already-closed frame sample; no getters or authority."""
    if (not _NATIVE_GRID_COHORT_DIAGNOSTICS
            or len(_NATIVE_GRID_COHORT_SEEN) >= _NATIVE_GRID_COHORT_LIMIT):
        return
    try:
        request = _NATIVE_GRID_COHORT_REQUEST.get()
        accepted = json.loads(accepted_view)
        requested = json.loads(request['viewToken']) if request else None
        digest = lambda text: hashlib.sha256(text.encode('utf-8')).hexdigest()
        requested_hash = digest(request['viewToken']) if request else None
        accepted_hash = digest(accepted_view)
        source_hash = digest(context)
        requested_camera = requested.get('camera') if requested else None
        accepted_camera = accepted.get('camera')
        lens = lambda camera: {k:camera.get(k) for k in ('fov','aspect','near','far')} if camera else None
        fingerprint = _cohort_json({'source':source_hash,'cameraPath':camera_path,
            'resolution':resolution,'fullResolution':full,'imageProjection':image_projection,
            'deliveredProjection':delivered_projection,'requestedLens':lens(requested_camera),
            'acceptedLens':lens(accepted_camera)})
        frame_number = frame['frameNumber']
        if type(frame_number) is not int or frame_number < 0:
            return
        owner_key = _cohort_json(owner)
        key = (owner_key,frame_number,requested_hash,accepted_hash,source_hash,fingerprint)
        if key in _NATIVE_GRID_COHORT_SEEN:
            return
        last = _NATIVE_GRID_COHORT_LAST.get(owner_key)
        if (last is not None and last['count'] >= 3 and last['fingerprint'] == fingerprint
                and frame_number >= last['frameNumber']
                and frame_number-last['frameNumber'] < 60):
            return
        record = {'schema':'dcdtuni.native-grid-cohort-diagnostic/v1',
            'sequence':len(_NATIVE_GRID_COHORT_SEEN)+1,'stage':stage,
            'owner':list(owner),'cameraPath':camera_path,'expectedCameraPath':expected_camera_path,
            'frameNumber':frame_number,'frameResolution':frame['resolution'],
            'resolution':resolution,'fullResolution':full,'textureMaps':maps,
            'requestedViewTokenSHA256':requested_hash,'acceptedViewTokenSHA256':accepted_hash,
            'requestedCamera':requested_camera,'acceptedCamera':accepted_camera,
            'sourceContextTokenSHA256':source_hash,
            'requestContextTokenSHA256':digest(request['contextToken']) if request else None,
            'requestSerial':request['requestSerial'] if request else None,
            'view':view,'deliveredView':frame['view'],
            'sampledImageProjection':image_projection,'deliveredProjection':delivered_projection,
            'sampledImageWorld':image_world,'deliveredWorld':delivered_world,
            'originalNativeCameraPredicateMatches':camera_matches,
            'fences':{'deliveredGetterClosingValues':True,'frameDimensions':True,
                      'initialApiCameraPath':True,'acceptedSourceViewFinalClosing':False},
            'additionalNativeReads':False,'gridPixelVerified':False,'diagnosticAuthority':False}
        encoded = _cohort_json(record)
        if len(encoded.encode('utf-8')) > 8192:
            return
        # Reserve before sink, so sink failure cannot bypass the eight-row cap.
        _NATIVE_GRID_COHORT_SEEN.add(key)
        _NATIVE_GRID_COHORT_LAST[owner_key] = {'frameNumber':frame_number,
            'fingerprint':fingerprint,'count':1 if last is None else last['count']+1}
        print('[dc.cfd_bridge] native-grid-cohort-diagnostic ' + encoded)
    except Exception:
        pass  # Trace failure cannot alter the existing sampler outcome.


def _native_grid_camera_comparison(view, delivered_view, image_world, delivered_world,
                                   image_projection, delivered_projection):
    """Diagnostics from the existing finite samples only; no native reads."""
    if not _NATIVE_GRID_DIAGNOSTICS:
        return None
    try:
        def difference(a, b):
            a, b = _numbers(a, 16), _numbers(b, 16)
            relative = [abs(x / max(1., abs(x), abs(y)) - y / max(1., abs(x), abs(y)))
                        for x, y in zip(a, b)]
            columns = sorted({i % 4 for i, (x, y) in enumerate(zip(a, b))
                              if abs(x-y) > 1e-6 * max(1., abs(x), abs(y))})
            return {'near':_near(a, b), 'maxRelativeDifference':max(relative),
                    'differingColumns':columns}
        image_projection, delivered_projection = (_numbers(image_projection, 16),
                                                  _numbers(delivered_projection, 16))
        transposed = [delivered_projection[j*4+i] for i in range(4) for j in range(4)]
        return {'view':difference(view, delivered_view),
                'imageWorld':difference(image_world, delivered_world),
                'imageProjection':difference(image_projection, delivered_projection),
                'imageProjectionVsTranspose':difference(image_projection, transposed),
                'sampledImageProjection':image_projection,
                'deliveredProjection':delivered_projection}
    except Exception:
        return None  # Diagnostic failure cannot change the sampler outcome.


def _native_grid_diagnostic(stage, error, camera_comparison=None):
    if not _NATIVE_GRID_DIAGNOSTICS or len(_NATIVE_GRID_DIAGNOSTICS_SEEN) >= _NATIVE_GRID_DIAGNOSTICS_LIMIT:
        return
    # Do not format native exceptions or arbitrary class names/payloads.
    names = {AttributeError:'AttributeError', KeyError:'KeyError', TypeError:'TypeError',
             ValueError:'ValueError', RuntimeError:'RuntimeError', ReferenceError:'ReferenceError',
             ImportError:'ImportError', OverflowError:'OverflowError', ZeroDivisionError:'ZeroDivisionError'}
    kind = names.get(type(error), 'Exception')
    detail, field = 'native-getter-or-validation-raised', None
    if type(error) in (AttributeError, ValueError, KeyError):
        args = error.args
        value = args[0] if type(args) is tuple and len(args) == 1 and type(args[0]) is str and len(args[0]) <= 128 else None
        fixed = {'missing frame':'delivered-frame-info-unavailable',
                 'native owner missing':'native-owner-unavailable',
                 'native image bound':'native-image-out-of-bounds',
                 'native texture mapping degenerate':'native-texture-mapping-degenerate',
                 'native texture mapping nonaffine':'native-texture-mapping-nonaffine',
                 'native values changed after delivered frame getter':'native-values-changed-after-frame',
                 'native texture frame dimensions not current':'native-frame-dimensions-not-current',
                 'native delivered frame camera not current':'native-frame-camera-not-current',
                 'non-finite public viewport observation':'public-observation-nonfinite',
                 'public viewport raster is not ready':'public-raster-unavailable'}
        detail = fixed.get(value, detail)
        if type(error) is KeyError and value in ('frame_number','resolution','view','projection'):
            detail, field = 'delivered-frame-field-unavailable', value
    key = (stage, kind, detail, field)
    if key in _NATIVE_GRID_DIAGNOSTICS_SEEN:
        return
    # Reserve first: a failed sink cannot bypass the module-lifetime cap.
    _NATIVE_GRID_DIAGNOSTICS_SEEN.add(key)
    try:
        record = {
            'schema':'dcdtuni.native-grid-diagnostic/v1', 'sequence':len(_NATIVE_GRID_DIAGNOSTICS_SEEN),
            'stage':stage, 'exceptionType':kind, 'detail':detail, 'frameField':field,
            'additionalNativeReads':False, 'gridPixelVerified':False,
        }
        if camera_comparison is not None:
            record['cameraComparison'] = camera_comparison
        encoded = json.dumps(record, separators=(',',':'), sort_keys=True, allow_nan=False)
        if len(encoded.encode('utf-8')) <= 8192:
            print('[dc.cfd_bridge] native-grid-diagnostic ' + encoded)
    except Exception:
        pass


def _native_frame_projection_matches(image_projection, delivered_projection,
                                     image_world, delivered_world):
    """Match all 16 finite entries in one common delivered depth convention.

    RTX reversed [0,1] depth is z=(w-z_GL)/2; X, Y and W are unchanged.
    Its 0<=z<=w planes are exactly the GL -w<=z_GL<=w planes. Only this
    fixed conversion (or unchanged GL) is accepted for BOTH matrices, so
    native geometry can retain the authored GL near/far clipping domain.
    No source/scene/stream-frame authority is supplied by this comparison.
    """
    image_projection, delivered_projection, image_world, delivered_world = (
        _numbers(values, 16) for values in
        (image_projection, delivered_projection, image_world, delivered_world))
    if (_near(image_projection, delivered_projection)
            and _near(image_world, delivered_world)):
        return True
    def reversed_depth(values):
        return _numbers([.5*(values[i+1]-values[i]) if i%4 == 2 else values[i]
                         for i in range(16)], 16)
    return (_near(reversed_depth(image_projection), delivered_projection)
            and _near(reversed_depth(image_world), delivered_world))


def sample_native_grid_viewport(viewport_api, expected_stage, expected_camera_path,
                                current_context, current_view, active_pair, viewport_window):
    """Readonly native-image sample with closing owner/source/view fences.

    This uses public delivered Hydra frame metadata, never outer UI rectangles,
    CSS, guessed crop, decoded dimensions or an encoder layout. No USD writes.
    Callbacks belong to the existing accepted-scene producer, not a wire dict.
    """
    from pxr import Gf, UsdGeom, CameraUtil
    from . import render_scene
    from .render_output_observations import _frame, _same_public_api
    def pending(reason):
        return {'phase':'pending','reason':reason,'nativeObservation':None,
                'gridPixelVerified':False,'encodedFrameProof':'unresolved'}
    def view_sample():
        view = current_view()
        render_scene.validate_view(view)
        grid = view.get('backgroundGrid')
        if grid is None or not grid['visible']:
            raise ValueError('native grid is not active')
        raster = view.get('backgroundGridRaster')
        revision = raster['binding']['viewRevision'] if raster else None
        token = json.dumps({k:view.get(k) for k in
            ('backgroundGrid','backgroundGridRaster','camera','timeSeconds')},
            sort_keys=True,separators=(',',':'),allow_nan=False)
        return token,render_scene._background_grid_intent_json(grid),revision
    def source_sample():
        return json.dumps(current_context(),sort_keys=True,separators=(',',':'),allow_nan=False)
    def pair_matches():
        api,window = active_pair()
        return (_same_public_api(api,viewport_api) and window is viewport_window
            and window is not None
            and _same_public_api(window.viewport_widget.viewport_api,viewport_api))
    diagnostic_stage = 'initial-source'
    diagnostic_camera_comparison = None
    def values(label):
        nonlocal diagnostic_stage, diagnostic_camera_comparison
        api = viewport_api
        diagnostic_stage = label + '-time'
        time = api.time
        time_key = ('default',) if time.IsDefault() else ('time',float(time.GetValue()))
        diagnostic_stage = label + '-owner'
        owner = (str(api.id),str(api.usd_context_name),str(api.render_product_path),time_key)
        if not owner[0] or not owner[2].startswith('/'):
            raise ValueError('native owner missing')
        diagnostic_stage = label + '-resolution'
        resolution = _size(api.resolution)
        diagnostic_stage = label + '-full-resolution'
        full = _size(api.full_resolution)
        if max(resolution) > 4096 or max(full) > 16384:
            raise ValueError('native image bound')
        diagnostic_stage = label + '-world-to-ndc'
        world = api.world_to_ndc
        diagnostic_stage = label + '-view'
        view = api.view
        diagnostic_stage = label + '-transform'
        transform = api.transform
        diagnostic_stage = label + '-projection'
        projection = api.projection
        maps = []
        diagnostic_stage = label + '-texture-map'
        for point in ((0.,0.),(1.,0.),(0.,1.),(-.5,.5),(.25,-.25)):
            uv,_ = api.map_ndc_to_texture(point)
            maps.append(_numbers(uv,2))
        o,x,y = maps[:3]
        ax,ay = [x[i]-o[i] for i in range(2)],[y[i]-o[i] for i in range(2)]
        if abs(ax[0]*ay[1]-ax[1]*ay[0]) <= 1e-12:
            raise ValueError('native texture mapping degenerate')
        for point,uv in zip(((-.5,.5),(.25,-.25)),maps[3:]):
            if not _near(uv,[o[i]+point[0]*ax[i]+point[1]*ay[i] for i in range(2)]):
                raise ValueError('native texture mapping nonaffine')
        diagnostic_stage = label + '-image-map'
        image_map = Gf.Matrix4d(2*ax[0],2*ax[1],0,0,2*ay[0],2*ay[1],0,0,
                              0,0,1,0,2*o[0]-1,2*o[1]-1,0,1)
        diagnostic_stage = label + '-frame-info'
        frame = _frame(api)  # last native frame getter; validate then close below
        # A delivered-frame getter can cross a texture/owner transition.
        # Close every earlier native observation after that final frame read.
        diagnostic_stage = label + '-closing-time'
        last_time = api.time
        last_time_key = ('default',) if last_time.IsDefault() else ('time',float(last_time.GetValue()))
        diagnostic_stage = label + '-closing-owner'
        last_owner = (str(api.id),str(api.usd_context_name),str(api.render_product_path),last_time_key)
        diagnostic_stage = label + '-closing-texture-map'
        last_maps = [_numbers(api.map_ndc_to_texture(point)[0],2) for point in
            ((0.,0.),(1.,0.),(0.,1.),(-.5,.5),(.25,-.25))]
        diagnostic_stage = label + '-closing-values'
        if (last_owner != owner or _size(api.resolution) != resolution or _size(api.full_resolution) != full
            or _matrix(api.world_to_ndc) != _matrix(world) or _matrix(api.view) != _matrix(view)
            or _matrix(api.transform) != _matrix(transform) or _matrix(api.projection) != _matrix(projection)
            or last_maps != maps):
            raise ValueError('native values changed after delivered frame getter')
        diagnostic_stage = label + '-frame-dimensions'
        if frame['resolution'] != resolution:
            raise ValueError('native texture frame dimensions not current')
        diagnostic_stage = label + '-frame-camera'
        image_world = _matrix(world*image_map)
        delivered = _matrix(Gf.Matrix4d(*frame['view'])*Gf.Matrix4d(*frame['projection']))
        image_projection = _matrix(projection*image_map)
        def trace_cohort(matches):
            if not _NATIVE_GRID_COHORT_DIAGNOSTICS:
                return
            try:
                _native_grid_cohort_diagnostic(label+'-frame-camera',owner,
                    diagnostic_camera_path,expected_camera_path,context,view_token[0],
                    frame,resolution,full,maps,_matrix(view),image_projection,
                    frame['projection'],image_world,delivered,matches)
            except Exception:
                pass  # Argument conversion is also diagnostic-only.
        if (not _near(_matrix(view),frame['view'])
                or not _native_frame_projection_matches(image_projection,frame['projection'],
                                                         image_world,delivered)):
            trace_cohort(False)
            if _NATIVE_GRID_DIAGNOSTICS:
                try:
                    diagnostic_camera_comparison = _native_grid_camera_comparison(
                        _matrix(view), frame['view'], image_world, delivered,
                        image_projection, frame['projection'])
                except Exception:
                    pass  # Keep the original validation failure on diagnostic failure.
            raise ValueError('native delivered frame camera not current')
        trace_cohort(True)
        return {'owner':owner,'resolution':resolution,'full':full,'world':_matrix(world),
            'view':_matrix(view),'transform':_matrix(transform),'projection':_matrix(projection),
            'maps':maps,'frame':frame,'image':image_world,'time':time}
    if viewport_api is None or viewport_window is None:
        return pending('native-viewport-unavailable')
    try:
        diagnostic_stage = 'initial-source'
        context = source_sample()
        diagnostic_stage = 'initial-view'
        view = view_sample()
        view_token = view  # Existing accepted value, not another producer callback.
        diagnostic_stage = 'initial-owner-fence'
        if (not pair_matches() or viewport_api.stage != expected_stage
                or (diagnostic_camera_path := str(viewport_api.camera_path)) != expected_camera_path):
            return pending('native-viewport-owner-not-current')
        first = values('first')
        diagnostic_stage = 'first-authored-camera'
        camera = UsdGeom.Camera(expected_stage.GetPrimAtPath(expected_camera_path))
        if not camera or not _near(first['view'],_matrix(camera.ComputeLocalToWorldTransform(first['time']).GetInverse())):
            return pending('native-camera-frame-not-current')
        diagnostic_stage = 'first-authored-camera-sample'
        authored = camera.GetCamera(first['time'])
        diagnostic_stage = 'first-camera-conform'
        CameraUtil.ConformWindow(authored,CameraUtil.MatchHorizontally,first['full'][0]/first['full'][1])
        diagnostic_stage = 'first-camera-projection'
        expected = _matrix(Gf.Matrix4d(*first['view'])*authored.frustum.ComputeProjectionMatrix())
        if not _near(first['image'],expected):
            return pending('native-projection-frame-not-current')
        # Repeat all native values after the delivered-frame getter and camera
        # getters. Close the accepted source/view and active public owner last.
        final = values('final')
        diagnostic_stage = 'final-authored-camera'
        latest_camera = camera.GetCamera(final['time'])
        diagnostic_stage = 'final-camera-conform'
        CameraUtil.ConformWindow(latest_camera,CameraUtil.MatchHorizontally,final['full'][0]/final['full'][1])
        diagnostic_stage = 'final-authored-view'
        latest_view = _matrix(camera.ComputeLocalToWorldTransform(final['time']).GetInverse())
        if not _near(latest_view,final['view']) or not _near(final['image'],
                _matrix(Gf.Matrix4d(*latest_view)*latest_camera.frustum.ComputeProjectionMatrix())):
            return pending('native-camera-changed-during-sample')
        diagnostic_stage = 'final-source-owner-view-fences'
        if (first != final or viewport_api.stage != expected_stage
            or str(viewport_api.camera_path) != expected_camera_path
            or not pair_matches() or view_sample() != view or source_sample() != context):
            return pending('native-observation-changed-during-sample')
        diagnostic_stage = 'native-observation'
        observation = NativeGridObservation(viewport_api,viewport_window,final['owner'],
            context,view[0],view[1],view[2],tuple(final['image']),tuple(final['resolution']),
            tuple(final['full']),final['frame']['frameNumber'])
        return {'phase':'ready','reason':None,'nativeObservation':observation,
                'gridPixelVerified':False,'encodedFrameProof':'unresolved'}
    except Exception as error:
        _native_grid_diagnostic(diagnostic_stage, error, diagnostic_camera_comparison)
        return pending('native-public-viewport-observation-unavailable')
