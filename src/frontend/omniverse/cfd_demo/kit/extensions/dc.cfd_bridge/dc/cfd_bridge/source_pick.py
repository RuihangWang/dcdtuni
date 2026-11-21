"""Source-only hit queries from the shared React viewport's real Shift clicks.

No keyboard synthesis, native selection mutation or result/pixel grants. Public
framebuffer -> current Window.frame NDC -> ViewportAPI texture mapping only.
Unknown stream/layout units refuse with bounded public measurements.
"""
import asyncio
import json
import math
import re
from dataclasses import dataclass

MAX_SAFE = 9007199254740991
SCHEMA = 'dcdtuni.source-pick/v1'


def _finite(v):
    if type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 16384:
        raise ValueError('invalid-public-measurement')
    return v


def _pair(v, positive=False, integer=False):
    a = list(v)
    if len(a) != 2:
        raise ValueError('invalid-public-measurement')
    a = [_finite(x) for x in a]
    if positive and any(x <= 0 for x in a):
        raise ValueError('invalid-public-measurement')
    if integer and any(type(x) is not int for x in a):
        raise ValueError('invalid-public-measurement')
    return a


def _token(v):
    raw = json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('source-context-limit')
    return raw


def parse_request(v):
    if type(v) is not dict or set(v) != {'binding','owner','rasterRevision','decoded','point','gestureId','signalPort'}:
        raise ValueError('invalid-source-pick-request')
    b = v['binding']
    if (type(b) is not dict or set(b) != {'projectUid','projectHash','sceneRevision','viewRevision'}
            or type(b['projectUid']) is not str or not 1 <= len(b['projectUid']) <= 128
            or type(b['projectHash']) is not str or not re.fullmatch('[0-9a-f]{64}', b['projectHash'])):
        raise ValueError('invalid-source-pick-binding')
    for n in ['sceneRevision','viewRevision']:
        if type(b[n]) is not int or not 0 <= b[n] <= MAX_SAFE:
            raise ValueError('invalid-source-pick-binding')
    if type(v['owner']) is not str or not 1 <= len(v['owner']) <= 128:
        raise ValueError('invalid-source-pick-owner')
    for n in ['rasterRevision','gestureId']:
        if type(v[n]) is not int or not 1 <= v[n] <= MAX_SAFE:
            raise ValueError('invalid-source-pick-sequence')
    if type(v['signalPort']) is not int or not 1 <= v['signalPort'] <= 65535:
        raise ValueError('invalid-source-pick-port')
    _pair(v['decoded'], True, True)
    p = _pair(v['point'])
    if any(x < 0 or x >= 1 for x in p):
        raise ValueError('source-pick-outside-video')
    return json.loads(_token(v))


@dataclass(frozen=True)
class Layout:
    api: object
    stage: object
    window: object
    widget: object
    frame: object
    delegate: object
    app: object
    measurement: str
    context: str
    view: str
    pixel: tuple


def sample_layout(api, window, app, workspace, getters, settings, paired, request,
                  read_context, read_view):
    from .render_output_observations import _same_public_api
    if getters is None or not getters['supports_viewport'](api) or not getters['supports_window'](window):
        raise ValueError('source-pick-unsupported-runtime')
    if not getters['supports_appwindow'](app) or not getters['is_app_window_attached'](app):
        raise ValueError('source-pick-app-unattached')
    current_api, current_window = paired()
    stage = api.stage
    widget, frame = window.viewport_widget, window.frame
    delegate = widget.display_delegate
    if (current_window is not window or not _same_public_api(current_api, api)
            or not _same_public_api(widget.viewport_api, api) or window.app_window is not app
            or stage is None or str(api.camera_path) != '/World/RenderCamera'):
        raise ValueError('source-pick-owner-changed')
    if (settings.get('/exts/omni.kit.livestream.app/primaryStream/streamType') != 'webrtc'
            or settings.get('/exts/omni.kit.livestream.app/primaryStream/signalPort') != request['signalPort']):
        raise ValueError('source-pick-not-owned-primary-stream')
    context, view = read_context(), read_view()
    b, raster = request['binding'], view.get('backgroundGridRaster')
    if (context.get('sceneRevision') != b['sceneRevision'] or context.get('projectHash') != b['projectHash']
            or context.get('projectUid') != b['projectUid'] or not isinstance(raster, dict)
            or raster['binding'] != dict(projectUid=b['projectUid'],projectContentSha256=b['projectHash'],
                sceneRevision=b['sceneRevision'],viewRevision=b['viewRevision'])
            or raster['output']['owner'] != request['owner']
            or raster['output']['revision'] != request['rasterRevision']
            or raster['output']['decoded'] != request['decoded']):
        raise ValueError('source-pick-source-view-not-current')
    context_token, view_token = _token(context), _token(view)

    def measure():
        return dict(appPosition=_pair(app.get_position()), appSize=_pair(app.get_size(), True, True),
            framebuffer=_pair((getters['get_framebuffer_width'](app),getters['get_framebuffer_height'](app)),True,True),
            mainPoints=_pair((workspace.get_main_window_width(),workspace.get_main_window_height()),True),
            scales=[_finite(workspace.get_dpi_scale()),_finite(app.get_dpi_scale()),_finite(app.get_ui_scale())],
            framePosition=_pair((frame.screen_position_x,frame.screen_position_y)),
            frameSize=_pair((frame.computed_width,frame.computed_height),True),
            frameContent=_pair((frame.computed_content_width,frame.computed_content_height),True),
            canvasSize=_pair(delegate.size,True),resolution=_pair(api.resolution,True,True))
    m = measure()
    final_api, final_window = paired()
    if (window.viewport_widget is not widget or window.frame is not frame or widget.display_delegate is not delegate
            or window.app_window is not app or final_window is not window or not _same_public_api(final_api,api)
            or not _same_public_api(widget.viewport_api,api) or api.stage is not stage or measure() != m
            or _token(read_view()) != view_token or _token(read_context()) != context_token):
        raise ValueError('source-pick-layout-changed')
    # This initial adapter supports the observed native unit-scale cohort only.
    # DOM DPI is handled separately; do not guess how arbitrary native UI units
    # convert to framebuffer pixels.
    if m['scales'] != [1,1,1] or m['mainPoints'] != m['appSize']:
        return None, 'source-pick-native-units-unresolved', m
    if m['frameSize'] != m['frameContent'] or m['canvasSize'] != m['frameContent']:
        return None, 'source-pick-frame-canvas-unresolved', m
    if m['framebuffer'] != request['decoded']:
        return None, 'source-pick-decoded-framebuffer-size-mismatch', m
    x = request['point'][0] * m['framebuffer'][0] + m['appPosition'][0]
    y = request['point'][1] * m['framebuffer'][1] + m['appPosition'][1]
    fx, fy = m['framePosition']; fw, fh = m['frameContent']
    if x < fx or y < fy or x >= fx+fw or y >= fy+fh:
        return None, 'source-pick-outside-native-frame', m
    pixel, matched_api = api.map_ndc_to_texture_pixel((2*(x-fx)/fw-1, 1-2*(y-fy)/fh))
    pixel = _pair(pixel, integer=True)
    if not _same_public_api(matched_api,api) or any(p<0 or p>=d for p,d in zip(pixel,m['resolution'])):
        return None, 'source-pick-native-letterbox', m
    final_api, final_window = paired()
    if (window.viewport_widget is not widget or window.frame is not frame or widget.display_delegate is not delegate
            or window.app_window is not app or final_window is not window or not _same_public_api(final_api,api)
            or not _same_public_api(widget.viewport_api,api) or api.stage is not stage or measure() != m
            or _token(read_view()) != view_token or _token(read_context()) != context_token):
        raise ValueError('source-pick-layout-changed')
    return Layout(api,stage,window,widget,frame,delegate,app,_token(m),context_token,view_token,tuple(pixel)), 'ready', m


class SourcePickController:
    def __init__(self):
        self.closed = False
        self.serial = 0
        self.owner = None
        self.last_gesture = 0

    def close(self):
        self.closed = True
        self.serial += 1

    async def query(self, raw, runtime, read_context, read_view, timeout=1.5):
        req = parse_request(raw)
        result = dict(schema=SCHEMA,gestureId=req['gestureId'],binding=req['binding'],owner=req['owner'],
            rasterRevision=req['rasterRevision'],status='refused',reason='source-pick-unresolved')
        def refusal(reason, measurements=None):
            result.update(status='refused',reason=reason)
            if measurements is not None:result['measurements']=measurements
            return result
        if self.closed:return refusal('source-pick-closed')
        try:
            api,window,app,workspace,getters,settings,paired=runtime()
            layout,reason,measurements=sample_layout(api,window,app,workspace,getters,settings,paired,req,read_context,read_view)
            if layout is None:return refusal(reason,measurements)
            if self.owner != req['owner']:
                self.owner,self.last_gesture=req['owner'],0
            if req['gestureId'] <= self.last_gesture:return refusal('source-pick-replayed')
            self.last_gesture=req['gestureId'];self.serial+=1;serial=self.serial
            from .render_output_observations import _frame
            from .grid_viewport import _near, _matrix, _native_frame_projection_matches
            from pxr import Gf,UsdGeom,CameraUtil
            stage=api.stage
            def camera_frame():
                f=_frame(api)
                if f['resolution'] != list(layout_measurements()['resolution']):raise ValueError('source-pick-frame-resolution-changed')
                camera=UsdGeom.Camera(stage.GetPrimAtPath('/World/RenderCamera'))
                t=api.time;authored=camera.GetCamera(t)
                CameraUtil.ConformWindow(authored,CameraUtil.MatchHorizontally,api.full_resolution[0]/api.full_resolution[1])
                av=_matrix(camera.ComputeLocalToWorldTransform(t).GetInverse());ap=_matrix(authored.frustum.ComputeProjectionMatrix())
                if not _near(f['view'],av) or not _native_frame_projection_matches(ap,f['projection'],
                        _matrix(Gf.Matrix4d(*av)*Gf.Matrix4d(*ap)),_matrix(Gf.Matrix4d(*f['view'])*Gf.Matrix4d(*f['projection']))):
                    raise ValueError('source-pick-camera-frame-not-current')
                return f,t,av,ap
            def layout_measurements():return json.loads(layout.measurement)
            first=camera_frame()
            first_closed,_,_=sample_layout(api,window,app,workspace,getters,settings,paired,req,read_context,read_view)
            if first_closed != layout:return refusal('source-pick-context-changed')
            future=asyncio.get_running_loop().create_future()
            def complete(path,position,pixel):
                if not future.done():future.set_result((path,position,pixel))
            accepted=api.request_query(layout.pixel,complete,query_name='dcdtuni.source-pick',
                view=first[0]['view'],projection=first[0]['projection'],add_outline=False,y_down=True)
            if accepted is not True:return refusal('source-pick-query-not-scheduled')
            path,position,pixel=await asyncio.wait_for(future,timeout)
            if self.closed or self.serial != serial:return refusal('source-pick-superseded')
            last=camera_frame()
            final,reason,_=sample_layout(api,window,app,workspace,getters,settings,paired,req,read_context,read_view)
            if final != layout:return refusal('source-pick-context-changed')
            if (api.stage is not stage or first[1:] != last[1:] or not _near(first[0]['view'],last[0]['view'])
                    or not _near(first[0]['projection'],last[0]['projection'])
                    or last[0]['frameNumber'] < first[0]['frameNumber'] or tuple(_pair(pixel,integer=True)) != layout.pixel):
                return refusal('source-pick-frame-changed')
            if not path:
                result.update(status='miss',reason='source-pick-empty');return result
            if type(path) is not str or not path.startswith('/World/RenderScene/Geometry/') or len(path)>1024:
                return refusal('source-pick-foreign-hit')
            prim=stage.GetPrimAtPath(path)
            if not prim or UsdGeom.Imageable(prim).ComputeVisibility()==UsdGeom.Tokens.invisible:
                return refusal('source-pick-missing-hidden-hit')
            prim_id,uid=prim.GetCustomDataByKey('dcdtuni:primitiveId'),prim.GetCustomDataByKey('dcdtuni:modelUid')
            c=read_context()
            if (type(prim_id) is not str or not 1<=len(prim_id)<=1024 or type(uid) is not str or not 1<=len(uid)<=256
                    or c['primitiveIds'].get(prim_id)!=uid or path not in c['uidPaths'].get(uid,())
                    or _token(c)!=layout.context or _token(read_view())!=layout.view):
                return refusal('source-pick-hit-binding-changed')
            current_prim=stage.GetPrimAtPath(path)
            if (current_prim!=prim or not current_prim
                    or current_prim.GetCustomDataByKey('dcdtuni:primitiveId')!=prim_id
                    or current_prim.GetCustomDataByKey('dcdtuni:modelUid')!=uid
                    or UsdGeom.Imageable(current_prim).ComputeVisibility()==UsdGeom.Tokens.invisible):
                return refusal('source-pick-hit-binding-changed')
            closed_layout,_,_=sample_layout(api,window,app,workspace,getters,settings,paired,req,read_context,read_view)
            if closed_layout != layout:return refusal('source-pick-context-changed')
            result.update(status='hit',reason='source-pick-owned-hit',event=dict(schema='dcdtuni.ov-edit/v1',
                sceneRevision=req['binding']['sceneRevision'],projectHash=req['binding']['projectHash'],
                coordinateSystem=dict(upAxis='Y',handedness='right',metersPerUnit=1),primId=prim_id,modelUid=uid,
                action=dict(type='pick')))
            return result
        except asyncio.TimeoutError:return refusal('source-pick-query-timeout')
        except Exception as error:
            reason=str(error)
            if not re.fullmatch('source-pick-[a-z-]{1,80}',reason):reason='source-pick-public-query-unavailable'
            return refusal(reason)
