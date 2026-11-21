"""Bounded, default-off, readonly observations. No USD writes or asset resolution.

An observation is not a compiled shader, encoded frame, or pixel proof. The
shared grid_viewport sampler owns all projection calculations.
"""
import json
import math
import re
import time

PROFILE = 'bounded-display-v1'
REQUEST_BYTES = 1024
REPLY_BYTES = 16384
SETTINGS = {
    'translucencyAsOpacity': '/rtx/material/translucencyAsOpacity',
    'translucencyEnabled': '/rtx/translucency/enabled',
    'raytracingFractionalCutoutOpacity': '/rtx/raytracing/fractionalCutoutOpacity',
    'pathtracingFractionalCutoutOpacity': '/rtx/pathtracing/fractionalCutoutOpacity',
}
ROOT = '/World/RenderScene'
CAMERA = '/World/RenderCamera'
_PATH = re.compile(r'^/World/RenderScene/Geometry/(p_[0-9a-f]{24})$')
_TOKEN = re.compile(r'^[A-Za-z0-9_.:+-]{1,64}$')
_ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
_UID = re.compile(r'^[^/\\\x00-\x1f]{1,128}$')
_HASH = re.compile(r'^[0-9a-f]{64}$')


def bounded_json(value, cap=REPLY_BYTES):
    text = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > cap:
        raise ValueError('reply-limit')
    return text


def parse_request(message):
    if not isinstance(message, str) or len(message) > REQUEST_BYTES or len(message.encode('utf-8')) > REQUEST_BYTES:
        raise ValueError('request-limit')
    value = json.loads(message)
    if type(value) is not dict or set(value) != {'requestId', 'diagnostics'} or not _ID.fullmatch(value.get('requestId', '')):
        raise ValueError('invalid-request')
    v = value['diagnostics']
    if type(v) is not dict or set(v) not in ({'profile', 'expectedSceneRevision', 'expectedProjectHash'}, {'profile', 'expectedSceneRevision', 'expectedProjectHash', 'roofModelUid'}):
        raise ValueError('invalid-request')
    if v['profile'] != PROFILE or type(v['expectedSceneRevision']) is not int or not 0 <= v['expectedSceneRevision'] <= 9007199254740991 or not isinstance(v['expectedProjectHash'], str) or not _HASH.fullmatch(v['expectedProjectHash']):
        raise ValueError('invalid-request')
    if 'roofModelUid' in v and (not isinstance(v['roofModelUid'], str) or not _UID.fullmatch(v['roofModelUid'])):
        raise ValueError('invalid-request')
    return value


def _unavailable(reason):
    return {'status': 'unavailable', 'reason': reason}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e18


def _array(value, size):
    try:
        out = list(value)
    except Exception:
        return None
    return out if len(out) == size and all(_finite(v) for v in out) else None


def _base_view(raw, expected_scene=None):
    # The normal producer is preserved. This branch alone bounds its reply.
    view = json.loads(bounded_json(raw, 4096))
    if type(view) is not dict or set(view) - {'camera', 'selectedUids', 'timeSeconds', 'backgroundGrid', 'backgroundGridRaster'}:
        raise ValueError('base-view-limit')
    selected = view.get('selectedUids')
    if type(selected) is not list or len(selected) > 8 or any(not isinstance(v, str) or not _UID.fullmatch(v) for v in selected):
        raise ValueError('base-view-limit')
    t = view.get('timeSeconds')
    if t is not None and (not _finite(t) or t < 0):
        raise ValueError('base-view-limit')
    if 'camera' in view:
        c = view['camera']
        if type(c) is not dict or set(c) != {'position','target','up','fov','aspect','near','far'} or any(_array(c[k],3) is None for k in ('position','target','up')):
            raise ValueError('base-view-limit')
        if any(not _finite(c[k]) for k in ('fov','aspect','near','far')) or not 0 < c['fov'] < 180 or c['aspect'] <= 0 or c['near'] <= 0 or c['far'] <= c['near']:
            raise ValueError('base-view-limit')
    if 'backgroundGrid' in view:
        g = view['backgroundGrid']
        if type(g) is not dict or set(g) != {'visible','spacingM','divisions','extentM','centerColor','lineColor','opacity','matrix'}:
            raise ValueError('base-view-limit')
        if type(g['visible']) is not bool or type(g['divisions']) is not int or not 4 <= g['divisions'] <= 4000 or _array(g['matrix'],16) is None:
            raise ValueError('base-view-limit')
        if not _finite(g['spacingM']) or not .01 <= g['spacingM'] <= 1 or not _finite(g['extentM']) or not 0 < g['extentM'] <= 2002 or not _finite(g['opacity']) or not 0 <= g['opacity'] <= 1:
            raise ValueError('base-view-limit')
        for k in ('centerColor','lineColor'):
            a = _array(g[k],3)
            if a is None or any(not 0 <= v <= 1 for v in a):
                raise ValueError('base-view-limit')
    if 'backgroundGridRaster' in view:
        # Reuse the common stored-view contract, not a second raster producer.
        # This metadata observes the client presentation; it proves no native
        # texture-to-encoded layout, viewport frame or pixel correctness.
        from . import render_scene
        render_scene.validate_view(view)
        binding = view['backgroundGridRaster']['binding']
        if expected_scene is not None and (binding['sceneRevision'], binding['projectContentSha256']) != expected_scene:
            raise ValueError('base-view-limit')
    return view


def _camera_identity(stage, api):
    """Fixed authored camera scalar identity; no projection or reply fallback.

    The normal viewJson fields can lag direct USD camera edits. Even an already
    pending delivered frame must not annotate a newer authored camera.
    """
    from pxr import Gf, UsdGeom
    camera = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA))
    if not camera or api is None:
        raise ValueError('invalid-public-value')
    authored = camera.GetCamera(api.time)
    projection = camera.GetProjectionAttr().Get(api.time)
    if projection not in ('perspective', 'orthographic') or authored.projection not in (Gf.Camera.Perspective, Gf.Camera.Orthographic):
        raise ValueError('invalid-public-value')
    scalars = (authored.focalLength, authored.horizontalAperture, authored.verticalAperture,
               authored.horizontalApertureOffset, authored.verticalApertureOffset)
    clipping = _array((authored.clippingRange.GetMin(), authored.clippingRange.GetMax()), 2)
    transform = _array([authored.transform[i][j] for i in range(4) for j in range(4)], 16)
    if any(not _finite(v) for v in scalars) or clipping is None or transform is None:
        raise ValueError('invalid-public-value')
    return (projection, tuple(scalars), tuple(clipping), tuple(transform))


def _context_owned(context, uid):
    c = context()
    paths = c['uidPaths'].get(uid, ()) if uid is not None else ()
    # Do not traverse or copy the scene; only the fixed requested UID entry.
    if type(paths) not in (tuple, list) or len(paths) > 4:
        raise ValueError('target-fanout')
    return (c['stage'], c['sceneRevision'], c['projectHash'], c['displayGeneration'], tuple(paths))


def _scalar_input(surface, field, material_path):
    attr = surface.GetAttribute('inputs:' + field)
    connections = list(attr.GetConnections()) if attr else []
    result = {'status': 'unavailable', 'reason': 'invalid-public-value', 'connected': bool(connections)}
    # This reads the authored scalar, never a file/sourceAsset input.
    value = attr.Get() if attr else None
    if _finite(value) and 0 <= value <= 1:
        result['authoredValue'] = value
    if not connections:
        if 'authoredValue' in result:
            result.update(status='observed', reason='none')
        return result
    if len(connections) != 1:
        result['reason'] = 'unsupported-connection'
        return result
    path = str(connections[0])
    prefix = material_path + '/'
    if not path.startswith(prefix) or '.outputs:' not in path:
        result['reason'] = 'unsupported-connection'
        return result
    shader_path, channel = path.rsplit('.outputs:', 1)
    if shader_path not in (material_path + '/ColorTexture', material_path + '/metalnessMap', material_path + '/roughnessMap') or channel not in ('r', 'g', 'b', 'a', 'rgb'):
        result['reason'] = 'unsupported-connection'
        return result
    source = surface.GetStage().GetPrimAtPath(shader_path)
    if not source or source.GetAttribute('info:id').Get() != 'UsdUVTexture':
        result['reason'] = 'unsupported-connection'
        return result
    result.update(status='observed', reason='none', sourcePath=shader_path, channel=channel)
    return result


def _opacity_texture(stage, scalar):
    result = {'status': 'unavailable', 'reason': 'unsupported-connection', 'assetInspection': 'not-requested'}
    path = scalar.get('sourcePath')
    if not path:
        return result
    prim = stage.GetPrimAtPath(path)
    if not prim or prim.GetAttribute('info:id').Get() != 'UsdUVTexture':
        return result
    result.update(status='observed', reason='none', sourcePath=path, channel=scalar['channel'])
    # Deliberately no 'file', sourceAsset, resolvedPath, layer, or resolver Get.
    for name in ('scale', 'bias'):
        attr = prim.GetAttribute('inputs:' + name)
        value = _array(attr.Get(), 4) if attr else None
        if value is None:
            return {'status': 'unavailable', 'reason': 'invalid-public-value', 'assetInspection': 'not-requested'}
        result[name] = value
    attr = prim.GetAttribute('inputs:sourceColorSpace')
    value = attr.Get() if attr else None
    if value not in ('raw', 'sRGB'):
        return {'status': 'unavailable', 'reason': 'invalid-public-value', 'assetInspection': 'not-requested'}
    result['colorSpace'] = value
    return result


def _roof(stage, paths, uid):
    missing = dict(_unavailable('target-missing'), targetSource='missing')
    if uid is None:
        return missing
    if not paths:
        return dict(_unavailable('target-not-acknowledged'), targetSource='missing')
    if len(paths) > 4 or len(set(paths)) != len(paths):
        return dict(_unavailable('target-fanout'), targetSource='missing')
    materials = []
    for path in paths:
        if not isinstance(path, str):
            return dict(_unavailable('not-owned-binding'), targetSource='missing')
        match = _PATH.fullmatch(path)
        if not match:
            return dict(_unavailable('not-owned-binding'), targetSource='missing')
        prim = stage.GetPrimAtPath(path)
        material_path = ROOT + '/Looks/' + match.group(1)
        surface_path = material_path + '/Surface'
        if not prim or prim.GetCustomDataByKey('dcdtuni:modelUid') != uid:
            return dict(_unavailable('not-owned-binding'), targetSource='missing')
        relationship = prim.GetRelationship('material:binding')
        if not relationship or [str(p) for p in relationship.GetTargets()] != [material_path]:
            return dict(_unavailable('not-owned-binding'), targetSource='missing')
        material = stage.GetPrimAtPath(material_path)
        output = material.GetAttribute('outputs:surface') if material else None
        if not output or [str(p) for p in output.GetConnections()] != [surface_path + '.outputs:surface']:
            return dict(_unavailable('not-owned-binding'), targetSource='missing')
        surface = stage.GetPrimAtPath(surface_path)
        if not surface or surface.GetAttribute('info:id').Get() != 'UsdPreviewSurface':
            return dict(_unavailable('unsupported-shader'), targetSource='missing')
        fields = {name: _scalar_input(surface, name, material_path) for name in ('opacity', 'metallic', 'opacityThreshold')}
        record = dict(geometryPath=path, materialPath=material_path, surfacePath=surface_path,
                      shaderId='UsdPreviewSurface', bindingPurpose='allPurpose', compiledShaderState='not-read', **fields)
        if fields['opacity']['connected']:
            record['opacityTexture'] = _opacity_texture(stage, fields['opacity'])
        materials.append(record)
    return dict(status='observed', reason='none', targetSource='single-selected-accepted-source-floor',
                modelUid=uid, primitiveCount=len(paths), materials=materials)


def _settings(settings, api):
    result = {'settingsScope': 'effective-carbs-settings; not compiled-shader proof', 'settings': {}}
    for name, path in SETTINGS.items():
        value = settings.get(path)
        result['settings'][name] = (dict(status='observed', reason='none', value=value) if type(value) is bool
                                    else _unavailable('missing-setting' if value is None else 'unexpected-setting-type'))
    for name, getter in [('viewportHydraEngine', lambda: api.hydra_engine), ('viewportRenderMode', lambda: api.render_mode), ('globalRtxMode', lambda: settings.get('/rtx/rendermode'))]:
        try:
            value = getter()
            if isinstance(value, str) and _TOKEN.fullmatch(value):
                result[name] = value
        except Exception:
            pass
    return result


class ReadbackController:
    """The strict native flag is sampled at normal extension startup only.

    Callables are owned synchronous readonly producers; identity is captured
    before all getters and checked after them. No subscription/timer/writer.
    """
    def __init__(self, enabled, monotonic=time.monotonic):
        self.__enabled = enabled is True
        self.__clock = monotonic
        self.__busy = False
        self.__last = None
        self.__sequence = 0

    def read(self, message, context, read_view, paired_viewport, settings, app_window, sampler, output_sampler=None):
        request = parse_request(message)
        rid = request['requestId']
        args = request['diagnostics']
        refusal = lambda reason: {'requestId': rid, 'ok': False, 'error': 'bounded-display:' + reason}
        if not self.__enabled:
            return refusal('disabled')
        start = self.__clock()
        if self.__busy or (self.__last is not None and start - self.__last < 1):
            return None  # no per-message rate-limit ACK flood
        self.__busy = True
        self.__last = start
        try:
            uid = args.get('roofModelUid')
            captured = _context_owned(context, uid)
            stage, revision, project_hash, generation, paths = captured
            if revision != args['expectedSceneRevision'] or project_hash != args['expectedProjectHash'] or stage is None:
                return refusal('source-context-changed')
            first_roof = _roof(stage, paths, uid)
            view = _base_view(read_view(), (revision, project_hash))
            api, window = paired_viewport()
            api_identity = (api.stage, str(api.camera_path), api.time) if api is not None else None
            camera_identity = _camera_identity(stage, api)
            renderer = _settings(settings, api)
            # All projection/mapping math belongs to the single frozen sampler.
            viewport = _lower_sampler(sampler(api, stage, CAMERA,
                current_scene_context={'sceneRevision': revision, 'projectHash': project_hash, 'displayGeneration': generation},
                viewport_window=window, app_window=app_window(), output_layout=None))
            output_observations = None
            if output_sampler is not None:
                output_observations = output_sampler(api, window, app_window,
                    expected_camera_path=CAMERA, read_context=context,
                    read_complete_view=lambda: _base_view(read_view(), (revision, project_hash)))
            elapsed = (self.__clock() - start) * 1000
            if not _finite(elapsed) or not 0 <= elapsed <= 1e9:
                return refusal('invalid-public-value')
            # Path/time alone do not bind pose, focal settings or a delivered
            # viewport frame. Re-read the same bounded producers after the last
            # timing callback; no extra projector or source/asset fallback.
            final_view = _base_view(read_view(), (revision, project_hash))
            final_renderer = _settings(settings, api)
            final_viewport = _lower_sampler(sampler(api, stage, CAMERA,
                current_scene_context={'sceneRevision': revision, 'projectHash': project_hash, 'displayGeneration': generation},
                viewport_window=window, app_window=app_window(), output_layout=None))
            final_camera_identity = _camera_identity(stage, api)
            if final_view != view or final_renderer != renderer or final_viewport != viewport or final_camera_identity != camera_identity:
                return refusal('source-context-changed')
            roof = _roof(stage, paths, uid)
            # Re-read primitive metadata only after all getters. A changed map
            # or public source identity cannot annotate a newer scene.
            if roof != first_roof:
                return refusal('source-context-changed')
            if api is not None and api_identity != (api.stage, str(api.camera_path), api.time):
                return refusal('source-context-changed')
            if captured != _context_owned(context, uid):
                return refusal('source-context-changed')
            self.__sequence += 1
            reply = {'requestId': rid, 'ok': True, 'view': view, 'sceneRevision': revision, 'projectHash': project_hash,
                     'diagnostics': {'schema': 'dcdtuni.render-display-diagnostics/v1', 'profile': PROFILE,
                        'sampleSequence': self.__sequence, 'sampleElapsedMs': elapsed,
                        'pixelVerified': False, 'renderer': renderer, 'viewport': viewport, 'roof': roof}}
            if output_observations is not None:
                # Owned producer, optional fixed observation schema. No output
                # layout/scene-frame permission follows from a stable sample.
                from .render_output_observations import _bounded
                reply['diagnostics']['renderOutputObservations'] = _bounded(output_observations)
            bounded_json(reply)
            return reply
        except ValueError as exc:
            reason = str(exc)
            return refusal(reason if reason in ('reply-limit', 'base-view-limit', 'target-fanout') else 'invalid-public-value')
        except Exception:
            return refusal('invalid-public-value')  # never serialize native exception/asset objects
        finally:
            self.__busy = False


def _lower_sampler(sample):
    """Strict output lowering of the frozen sampler, with no projector here."""
    unavailable = dict(_unavailable('invalid-public-value'), layoutProof='unresolved', textureToEncodedMapping='unresolved')
    if type(sample) is not dict or type(sample.get('observations')) is not dict:
        return unavailable
    obs = sample['observations']
    # Native layout is deliberately not supplied by the wire or this diagnostic.
    reason = sample.get('reason')
    reason = {'stream-layout-unresolved': 'layout-unresolved', 'viewport-unavailable': 'no-viewport',
              'viewport-scene-not-current': 'source-context-changed',
              'viewport-camera-unavailable': 'camera-mismatch',
              'viewport-camera-frame-not-current': 'source-context-changed',
              'viewport-projection-frame-not-current': 'source-context-changed'}.get(reason, 'invalid-public-value')
    out = dict(status='pending', reason=reason, layoutProof='unresolved', textureToEncodedMapping='unresolved')
    if sample.get('phase') != 'pending' or obs.get('layoutProof') != 'unresolved':
        return unavailable
    if 'stageSame' in obs:
        if type(obs['stageSame']) is not bool:
            return unavailable
        out['stageSame'] = obs['stageSame']
    if 'cameraPath' in obs:
        if obs['cameraPath'] != CAMERA:
            return dict(_unavailable('camera-mismatch'), layoutProof='unresolved', textureToEncodedMapping='unresolved')
        out.update(cameraPath=CAMERA, expectedCameraSame=True)
    for name in ('resolution', 'fullResolution', 'appSizePx'):
        if name in obs:
            values = _array(obs[name], 2)
            if values is None or any(type(v) is not int or not 1 <= v <= 32768 for v in values):
                return unavailable
            out[name] = values
    for name, target in [('view', 'view'), ('uiProjection', 'uiProjection'), ('worldToNdc', 'worldToNdc'),
                         ('imageWorldToNdc', 'imageWorldToNdc'), ('transform', 'cameraTransform')]:
        if name in obs:
            values = _array(obs[name], 16)
            if values is None:
                return unavailable
            out[target] = values
            out['matrixConvention'] = 'Gf-row-vector; row-major-16'
    for name, count in [('appPositionScreenPx', 2), ('outerWindowRectObserved', 4)]:
        if name in obs:
            values = _array(obs[name], count)
            if values is None:
                return unavailable
            out[name] = values
    for name in ('appDpiScale', 'appUiScale'):
        if name in obs:
            if not _finite(obs[name]) or not 0 < obs[name] <= 64:
                return unavailable
            out[name] = obs[name]
    if 'ndcToTextureSamples' in obs:
        values = obs['ndcToTextureSamples']
        if type(values) is not list or not 3 <= len(values) <= 5:
            return unavailable
        samples = []
        for value in values:
            if type(value) is not dict or set(value) != {'ndc', 'uv', 'insideTexture'}:
                return unavailable
            ndc, uv = _array(value['ndc'], 2), _array(value['uv'], 2)
            if ndc is None or uv is None or type(value['insideTexture']) is not bool:
                return unavailable
            samples.append(dict(ndc=ndc, uv=uv, insideTexture=value['insideTexture']))
        out['ndcToTextureSamples'] = samples
    return out
