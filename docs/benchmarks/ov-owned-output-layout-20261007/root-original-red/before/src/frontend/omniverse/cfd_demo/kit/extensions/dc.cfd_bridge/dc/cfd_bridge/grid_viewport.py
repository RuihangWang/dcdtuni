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
    obs = {'layoutProof':'unresolved'}
    def pending(reason):
        return {'phase':'pending','reason':reason,'observations':obs,'projection':None}
    if viewport_api is None:
        return pending('viewport-unavailable')
    try:
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
        if not hasattr(output_layout,'capture_current'):
            return pending('stream-layout-unresolved')
        layout = output_layout.capture_current(viewport_api,viewport_window,app_window)
        if layout is None:
            return pending('stream-layout-unresolved')
        transform = Gf.Matrix4d(*_numbers(layout['imageNdcToOutputNdc'],16))
        if abs(transform.GetDeterminant()) <= 1e-12:
            return pending('stream-layout-degenerate')
        obs['layoutProof'] = 'owned-native-frame'
        obs['imageNdcToOutputNdc'] = _matrix(transform)
        return {'phase':'ready','reason':None,'observations':obs,
                'projection':_matrix(image_world*transform)}
    except Exception:
        # Fixed reason is safe for the UI; arbitrary native payload is not logged.
        return pending('public-viewport-observation-unavailable')
