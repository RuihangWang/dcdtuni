"""CPU-only contract and actual OpenUSD authoring tests; no Kit/RTX imports."""
import copy
import json
import os
import sys
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'kit/extensions/dc.cfd_bridge'))
from dc.cfd_bridge.render_scene import validate_snapshot, author_scene, SceneTransfer, apply_view  # noqa: E402


def packet():
    return {
        'schema': 'dcdtuni.render-scene/v1',
        'project': {'uid': 'project-1', 'name': 'mixed'},
        'coordinateSystem': {'upAxis': 'Y', 'metersPerUnit': 1, 'handedness': 'right'},
        'primitives': [{
            'id': 'rack/triangle', 'modelUid': 'rack-1', 'name': 'same name', 'kind': 'triangles',
            'positions': [0, 0, 0, 1, 0, 0, 0, 1, 0], 'indices': [0, 1, 2],
            'normals': [0, 0, 1] * 3, 'uvs': [0, 0, 1, 0, 0, 1],
            'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3, 1, 2, 1],
            'visible': True,
            'material': {'color': [1, 0, 0], 'emissive': [0, 0, 0], 'opacity': .6,
                         'roughness': .4, 'metalness': .7, 'doubleSided': True, 'unlit': False},
        }],
        'textures': {}, 'view': {'selectedUids': ['rack-1'], 'timeSeconds': 12.5}, 'warnings': [],
    }


def test_schema_rejects_wrong_frame_unknown_keys_nan_and_invalid_indices():
    original = packet()
    validate_snapshot(original)
    for mutate in [
        lambda p: p['coordinateSystem'].update(metersPerUnit=.01),
        lambda p: p.update(cfdrun={}),
        lambda p: p['primitives'][0]['positions'].__setitem__(0, float('nan')),
        lambda p: p['primitives'][0]['indices'].__setitem__(0, 12),
    ]:
        p = copy.deepcopy(original)
        mutate(p)
        with pytest.raises(ValueError):
            validate_snapshot(p)


def test_actual_usd_coordinates_materials_uid_and_time(tmp_path):
    from pxr import Gf, Usd, UsdGeom, UsdShade
    stage = Usd.Stage.CreateInMemory()
    p = packet()
    other = copy.deepcopy(p['primitives'][0])
    other.update(id='pipe/triangle', modelUid='pipe-1')
    p['primitives'].append(other)
    result = author_scene(stage, p, tmp_path)
    assert set(result['uidPaths']) == {'rack-1', 'pipe-1'}
    prim = stage.GetPrimAtPath(result['uidPaths']['rack-1'][0])
    assert prim.GetCustomDataByKey('dcdtuni:modelUid') == 'rack-1'
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
    assert tuple(matrix.Transform(Gf.Vec3d(0, 0, 0))) == pytest.approx((3, 1, 2))
    mesh = UsdGeom.Mesh(prim)
    assert list(mesh.GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    surface = UsdShade.Shader(material.GetPrim().GetChild('Surface'))
    assert surface.GetInput('roughness').Get() == pytest.approx(.4)
    assert surface.GetInput('metallic').Get() == pytest.approx(.7)
    from dc.cfd_bridge.render_scene import rtx_cutout_opacity
    double_sided = p['primitives'][0]['material']['doubleSided']
    assert surface.GetInput('opacity').Get() == pytest.approx(rtx_cutout_opacity(.6, double_sided))
    assert not prim.GetAttribute('primvars:doNotCastShadows')  # 0.6 still casts, as in the editor
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1
    assert UsdGeom.GetStageUpAxis(stage) == 'Y'
    assert stage.GetPrimAtPath('/World/RenderScene').GetCustomDataByKey('dcdtuni:timeSeconds') == 12.5


def test_cutout_opacity_shows_what_three_shows_behind_a_surface():
    """Three shows (1 - a) behind a transparent surface, (1 - a)^2 behind a
    double-sided one (two passes); the RTX cutout is chosen from the measured
    transmission so Kit shows the same."""
    from dc.cfd_bridge.render_scene import RTX_CUTOUT_TRANSMISSION, rtx_cutout_opacity

    def shown(cutout):
        for (a0, t0), (a1, t1) in zip(RTX_CUTOUT_TRANSMISSION, RTX_CUTOUT_TRANSMISSION[1:]):
            if a0 <= cutout <= a1:
                return t0 + (t1 - t0) * (cutout - a0) / (a1 - a0)

    for alpha in (.1, .3, .37, .6, .85):
        for double_sided in (False, True):
            cutout = rtx_cutout_opacity(alpha, double_sided)
            assert 0 < cutout < 1
            assert shown(cutout) == pytest.approx((1 - alpha) ** (2 if double_sided else 1))
    assert rtx_cutout_opacity(.6, True) == pytest.approx(.537, abs=.005)  # Shinaide's ceiling
    assert [rtx_cutout_opacity(a, True) for a in (0, 1)] == [0, 1]
    cutouts = [rtx_cutout_opacity(a / 20, False) for a in range(21)]
    assert cutouts == sorted(cutouts)


def test_translucent_colour_is_scaled_to_three_display_space_blend():
    """Linear-light blending at RTX's coverage of a light grey gives what
    Three's display-space blend gives; opaque colours are untouched."""
    from dc.cfd_bridge import render_scene as r
    lin = r._srgb_to_linear
    ref = r.TRANSLUCENT_REFERENCE
    for alpha, double_sided in ((.37, True), (.6, True), (.85, False)):
        scale = r.translucent_albedo_scale(alpha, double_sided)
        coverage = 1 - (1 - r.rtx_cutout_opacity(alpha, double_sided)) ** 2
        three = alpha + ((1 - alpha) * alpha * r.THREE_BACK_PASS if double_sided else 0)
        assert 0 < scale < 1
        assert coverage * scale * lin(ref) == pytest.approx(lin(three * ref))
    assert r.translucent_albedo_scale(1.0, True) == 1.0
    look = r.kit_look({'opacity': .37, 'doubleSided': True, 'color': [.8, .8, .8]})
    assert look['color'] == pytest.approx([.8 * r.translucent_albedo_scale(.37, True)] * 3)
    assert r.kit_look({'opacity': 1, 'color': [.8, .8, .8]})['color'] == [.8, .8, .8]


def test_see_through_surfaces_and_the_room_shell_cast_no_shadow(tmp_path):
    """The editor's shadow rule (scene_lighting.js): under 0.6 opacity a surface
    casts none, and the room's own walls and ceiling receive shadows only."""
    from pxr import Usd
    from dc.cfd_bridge.render_scene import casts_no_shadow
    base = packet()['primitives'][0]['material']
    assert casts_no_shadow(dict(base, opacity=.37)) is True
    assert casts_no_shadow(dict(base, opacity=.6)) is False
    assert casts_no_shadow(dict(base, opacity=.37, alphaTest=.5)) is False
    assert casts_no_shadow(dict(base, opacity=1.0), 'room-shell') is True
    p = packet()
    shell = copy.deepcopy(p['primitives'][0])
    shell.update(id='room/ceiling', modelUid='room-1', displayRole='room-shell')
    shell['material'] = dict(shell['material'], opacity=1.0)
    p['primitives'].append(shell)
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, p, tmp_path)
    ceiling = stage.GetPrimAtPath(receipt['uidPaths']['room-1'][0])
    assert ceiling.GetAttribute('primvars:doNotCastShadows').Get() is True


def test_invalid_scene_does_not_destroy_previous_stage(tmp_path):
    from pxr import Usd
    stage = Usd.Stage.CreateInMemory()
    author_scene(stage, packet(), tmp_path)
    before = stage.GetRootLayer().ExportToString()
    p = packet()
    p['primitives'][0]['indices'] = [999, 1, 2]
    with pytest.raises(ValueError):
        author_scene(stage, p, tmp_path)
    assert stage.GetRootLayer().ExportToString() == before


def test_scene_transfer_rejects_out_of_order_chunks_and_returns_exact_packet():
    transfer = SceneTransfer()
    text = json.dumps(packet())
    transfer.begin('job', 2)
    with pytest.raises(ValueError):
        transfer.chunk('job', 1, text[20:])
    transfer.begin('job', 2)
    transfer.chunk('job', 0, text[:20])
    transfer.chunk('job', 1, text[20:])
    assert transfer.finish('job') == packet()


def test_deflated_scene_transfer_inflates_to_the_exact_packet_within_the_bound():
    import base64
    import zlib
    transfer = SceneTransfer()

    def send(raw, encoding='deflate-base64'):
        data = base64.b64encode(raw).decode()
        step = min(12000, -(-len(data) // 3))
        pieces = [data[i:i + step] for i in range(0, len(data), step)]
        transfer.begin('job', len(pieces), encoding)
        for index, piece in enumerate(pieces):
            transfer.chunk('job', index, piece)
        return transfer.finish('job')

    text = json.dumps(packet())
    assert send(zlib.compress(text.encode())) == packet()
    with pytest.raises(ValueError, match='Truncated'):
        send(zlib.compress(text.encode())[:-6])
    with pytest.raises(ValueError, match='Corrupt'):
        send(b'not a zlib stream')
    # A small stream that inflates past 128 MiB is refused, not inflated.
    bomb = zlib.compress(b' ' * (SceneTransfer.LIMIT + 1024), 9)
    assert len(bomb) < 1024 * 1024
    with pytest.raises(ValueError, match='128 MiB'):
        send(bomb)
    with pytest.raises(ValueError, match='encoding'):
        transfer.begin('job', 1, 'gzip')


def test_camera_is_authored_in_project_coordinates(tmp_path):
    from pxr import Gf, Usd, UsdGeom
    stage = Usd.Stage.CreateInMemory()
    p = packet()
    author_scene(stage, p, tmp_path)
    p['view']['camera'] = {'position': [5, 3, 12], 'target': [5, 0, 4], 'up': [0, 1, 0],
                           'fov': 50, 'aspect': 1.5, 'near': .1, 'far': 500}
    apply_view(stage, p['view'])
    cam = UsdGeom.Camera(stage.GetPrimAtPath('/World/RenderCamera'))
    matrix = cam.GetLocalTransformation()
    assert tuple(matrix.Transform(Gf.Vec3d(0, 0, 0))) == pytest.approx((5, 3, 12))
    assert cam.GetClippingRangeAttr().Get() == Gf.Vec2f(.1, 500)


def test_texture_assets_and_shader_inputs_survive_usd_serialization(tmp_path):
    from pxr import Sdf, Usd, UsdShade
    p = packet()
    png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6ZAAAAABJRU5ErkJggg=='
    p['textures'] = {'color': png, 'normal': png, 'roughness': png, 'metalness': png}
    p['primitives'][0]['material'].update(map='color', normalMap='normal', normalScale=[2, -1], alphaTest=.2,
                                         mapColorSpace='srgb', normalColorSpace='linear', wrapS='mirror', wrapT='clamp',
                                         roughnessMap='roughness', metalnessMap='metalness')
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, p, tmp_path)
    file = tmp_path / 'scene.usda'
    stage.GetRootLayer().Export(str(file))
    reopened = Usd.Stage.Open(str(file))
    prim = reopened.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    color = UsdShade.Shader(material.GetPrim().GetChild('ColorTexture'))
    normal = UsdShade.Shader(material.GetPrim().GetChild('NormalTexture'))
    assert color.GetInput('sourceColorSpace').Get() == 'sRGB'
    assert color.GetInput('wrapS').Get() == 'mirror'
    assert color.GetInput('wrapT').Get() == 'clamp'
    assert tuple(normal.GetInput('scale').Get()) == (4, -2, 2, 1)
    assert tuple(normal.GetInput('bias').Get()) == (-2, 1, -1, 0)
    assert normal.GetInput('sourceColorSpace').Get() == 'raw'
    asset = color.GetInput('file').Get()
    assert isinstance(asset, Sdf.AssetPath)
    assert os.path.exists(asset.path)
    assert UsdShade.Shader(material.GetPrim().GetChild('Surface')).GetInput('normal').HasConnectedSource()
    surface = UsdShade.Shader(material.GetPrim().GetChild('Surface'))
    roughness = surface.GetInput('roughness').GetValueProducingAttributes()
    metalness = surface.GetInput('metallic').GetValueProducingAttributes()
    assert len(roughness) == 1 and roughness[0].GetPath() == material.GetPath().AppendPath('roughnessMap').AppendProperty('outputs:g')
    assert len(metalness) == 1 and metalness[0].GetPath() == material.GetPath().AppendPath('metalnessMap').AppendProperty('outputs:b')
    assert tuple(UsdShade.Shader(roughness[0].GetPrim()).GetInput('scale').Get()) == pytest.approx((.4, .4, .4, 1))
    assert tuple(UsdShade.Shader(metalness[0].GetPrim()).GetInput('scale').Get()) == pytest.approx((.7, .7, .7, 1))


def test_curve_point_and_vertex_colour_authoring(tmp_path):
    from pxr import Usd, UsdGeom
    p = packet()
    p['primitives'][0]['colors'] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    for kind, indices in [('lines', [0, 1, 1, 2]), ('points', [0, 2])]:
        extra = copy.deepcopy(p['primitives'][0])
        extra.update(id=kind, kind=kind, indices=indices, modelUid=kind, width=.004)
        p['primitives'].append(extra)
    stage = Usd.Stage.CreateInMemory()
    result = author_scene(stage, p, tmp_path)
    lines = UsdGeom.BasisCurves(stage.GetPrimAtPath(result['uidPaths']['lines'][0]))
    assert list(lines.GetCurveVertexCountsAttr().Get()) == [2, 2]
    assert len(lines.GetPointsAttr().Get()) == 4
    points = UsdGeom.Points(stage.GetPrimAtPath(result['uidPaths']['points'][0]))
    assert len(points.GetPointsAttr().Get()) == 2
    assert list(points.GetWidthsAttr().Get()) == pytest.approx([.004])


JPEG_1PX = ('data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDwKiiivzI/uM//2Q==')


def test_jpeg_textures_are_cached_as_jpeg_and_mislabelled_bytes_are_refused(tmp_path):
    from pxr import Usd
    p = packet()
    p['textures'] = {'color': JPEG_1PX}
    p['primitives'][0]['material']['map'] = 'color'
    validate_snapshot(p)
    receipt = author_scene(Usd.Stage.CreateInMemory(), p, tmp_path)
    from dc.cfd_bridge import render_scene
    import hashlib
    jpeg, _ = render_scene.texture_bytes(JPEG_1PX)
    mdl = Path(render_scene.__file__).with_name('DcdtuniFractionalPreview.mdl').read_bytes()
    expected = {hashlib.sha256(jpeg).hexdigest() + '.jpg',
                'DcdtuniFractionalPreview_' + hashlib.sha256(mdl).hexdigest()[:16] + '.mdl'}
    assert {f.name for f in tmp_path.iterdir()} == expected
    assert (tmp_path / (hashlib.sha256(jpeg).hexdigest() + '.jpg')).read_bytes() == jpeg
    assert receipt['primitives'] == 1
    for wrong in (JPEG_1PX.replace('image/jpeg', 'image/png'),
                  'data:image/jpeg;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6ZAAAAABJRU5ErkJggg==',
                  JPEG_1PX.replace('image/jpeg', 'image/gif')):
        p['textures'] = {'color': wrong}
        with pytest.raises(ValueError):
            validate_snapshot(p)


def test_exported_texture_bundle_can_move_between_hosts(tmp_path):
    from pxr import Usd, UsdShade
    packet_data = packet()
    packet_data['textures'] = {'color': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6ZAAAAABJRU5ErkJggg=='}
    packet_data['primitives'][0]['material']['map'] = 'color'
    packet_data['view']['backgroundGrid'] = _fractional_grid()
    original = tmp_path / 'original'
    original.mkdir()
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, packet_data, original / 'scene_textures', asset_prefix='scene_textures')
    stage.GetRootLayer().Export(str(original / 'scene.usda'))
    moved = tmp_path / 'moved'
    shutil.move(str(original), str(moved))
    assert not original.exists()
    reopened = Usd.Stage.Open(str(moved / 'scene.usda'))
    prim = reopened.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    texture = UsdShade.Shader(material.GetPrim().GetChild('ColorTexture'))
    asset = texture.GetInput('file').Get()
    assert not Path(asset.path).is_absolute()
    assert asset.path.startswith('scene_textures/')
    assert Path(asset.resolvedPath).is_file()
    assert Path(asset.resolvedPath).parent == moved / 'scene_textures'
    from dc.cfd_bridge import render_scene
    for material_path in (material.GetPath(), render_scene.BACKGROUND_GRID_ROOT + '/Looks/Grid'):
        native = UsdShade.Shader(reopened.GetPrimAtPath(str(material_path) + '/FractionalSurface'))
        mdl_asset = native.GetSourceAsset('mdl')
        assert not Path(mdl_asset.path).is_absolute() and mdl_asset.path.startswith('scene_textures/')
        assert Path(mdl_asset.resolvedPath).is_file() and Path(mdl_asset.resolvedPath).parent == moved / 'scene_textures'
    assert reopened.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:fractionalMaterialAsset') == mdl_asset.path


def test_colour_override_is_per_primitive_and_restores_connections(tmp_path):
    """Liquid result colouring paints each primitive's own material and undoes
    exactly what it changed, including a vertex-colour connection. Primitives
    share one material per look, so painting copies it out first: a neighbour
    bound to the same look keeps its colour."""
    from pxr import Gf, Usd, UsdShade
    from dc.cfd_bridge.render_scene import index_render_prims, override_colours, restore_colours
    stage = Usd.Stage.CreateInMemory()
    p = packet()
    pipe = copy.deepcopy(p['primitives'][0])
    pipe.update(id='pipe/tube', modelUid='pipe-1')
    pipe['material'] = dict(pipe['material'], color=[.5, .5, .5])
    valve = copy.deepcopy(p['primitives'][0])
    valve.update(id='pipe/valve', modelUid='pipe-1', equipmentUid='valve-7', colors=[0, 1, 0] * 3)
    unlit = copy.deepcopy(p['primitives'][0])
    unlit['material'] = dict(unlit['material'], unlit=True, color=[.2, .3, .4])
    unlit.update(id='label/unlit', modelUid='label-1')
    twin = copy.deepcopy(pipe)
    twin.update(id='pipe/twin', modelUid='pipe-2')  # the tube's look, another model
    p['primitives'] += [pipe, valve, unlit, twin]
    author_scene(stage, p, tmp_path)
    index = index_render_prims(stage)
    assert set(index) == {('rack-1', '', ''), ('pipe-1', '', ''), ('pipe-1', 'valve-7', ''), ('label-1', '', ''),
                          ('pipe-2', '', '')}

    def bound(path):
        return str(UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path)).ComputeBoundMaterial()[0].GetPath())

    def attr(path, name):
        material = stage.GetPrimAtPath(bound(path))
        carrier = material if material.GetChild('FractionalSurface') else material.GetChild('Surface')
        return carrier.GetAttribute('inputs:' + name)

    tube = index[('pipe-1', '', '')][0]
    fitting = index[('pipe-1', 'valve-7', '')][0]
    label = index[('label-1', '', '')][0]
    rack = index[('rack-1', '', '')][0]
    twin_path = index[('pipe-2', '', '')][0]
    shared_look = bound(tube)
    assert bound(twin_path) == shared_look and shared_look.rsplit('/', 1)[-1].startswith('m_')
    assert len(stage.GetPrimAtPath('/World/RenderScene/Looks').GetChildren()) == 4  # 5 primitives, 4 looks
    rack_before = tuple(attr(rack, 'diffuseColor').Get())
    assert attr(fitting, 'diffuseColor').GetConnections()  # vertex colours drive it
    saved = {}
    tube_emissive_before = tuple(attr(tube, 'emissiveColor').Get())
    n = override_colours(stage, {tube: (1, 0, 0), fitting: (0, 0, 1), label: (1, 1, 0)}, saved, glow=0.5)
    assert n == 3
    assert tuple(attr(tube, 'diffuseColor').Get()) == (1, 0, 0)
    assert tuple(attr(tube, 'emissiveColor').Get()) == pytest.approx((.5, 0, 0))  # glow: readable in shadow
    assert not attr(fitting, 'diffuseColor').GetConnections()
    assert tuple(attr(label, 'emissiveColor').Get()) == (1, 1, 0)  # unlit paints emissive
    assert tuple(attr(rack, 'diffuseColor').Get()) == rack_before  # untouched neighbour
    assert bound(tube) == '/World/RenderScene/Looks/' + tube.rsplit('/', 1)[-1]  # its own copy
    assert bound(twin_path) == shared_look  # the look's other user is not painted
    # The shared look keeps its (translucent-calibrated) colour.
    from dc.cfd_bridge.render_scene import translucent_albedo_scale
    twin_grey = .5 * translucent_albedo_scale(p['primitives'][0]['material']['opacity'], p['primitives'][0]['material']['doubleSided'])
    assert tuple(attr(twin_path, 'diffuseColor').Get()) == pytest.approx((twin_grey,) * 3)
    vertex = attr(fitting, 'diffuseColor').GetPrim().GetPath()
    assert str(vertex).startswith('/World/RenderScene/Looks/' + fitting.rsplit('/', 1)[-1])
    override_colours(stage, {tube: (0, 1, 0)}, saved)  # repaint keeps the FIRST original
    restore_colours(stage, saved)
    assert saved == {}
    assert bound(tube) == shared_look and not stage.GetPrimAtPath('/World/RenderScene/Looks/' + tube.rsplit('/', 1)[-1])
    assert tuple(attr(tube, 'diffuseColor').Get()) == pytest.approx((twin_grey,) * 3)
    assert tuple(attr(tube, 'emissiveColor').Get()) == pytest.approx(tube_emissive_before)
    assert attr(fitting, 'diffuseColor').GetConnections()
    label_scale = translucent_albedo_scale(unlit['material']['opacity'], unlit['material']['doubleSided'])
    assert tuple(attr(label, 'emissiveColor').Get()) == pytest.approx(tuple(c * label_scale for c in (.2, .3, .4)))


def test_liquid_part_is_validated_and_lands_in_custom_data(tmp_path):
    from pxr import Usd
    from dc.cfd_bridge.render_scene import index_render_prims
    p = packet()
    p['primitives'][0]['liquidPart'] = 'supply-manifold'
    validate_snapshot(p)
    stage = Usd.Stage.CreateInMemory()
    author_scene(stage, p, tmp_path)
    assert ('rack-1', '', 'supply-manifold') in index_render_prims(stage)
    bad = copy.deepcopy(p)
    bad['primitives'][0]['liquidPart'] = 'chiller'
    with pytest.raises(ValueError):
        validate_snapshot(bad)


@pytest.mark.parametrize('field, bad', [
    ('positions', float('nan')), ('positions', float('inf')), ('positions', True), ('positions', '1'),
    ('indices', -1), ('indices', 1.0), ('indices', False),
])
def test_numeric_array_fast_path_rejects_what_the_element_walk_rejected(field, bad):
    p = packet()
    p['primitives'][0][field][0] = bad
    with pytest.raises(ValueError, match=rf"\$\.primitives\[0\]\.{field}\[0\]"):
        validate_snapshot(p)


@pytest.mark.parametrize('opacity', [.1, .2, .6])
def test_fractional_constant_and_alpha_test_use_shared_real_inputs(tmp_path, opacity):
    """USD graph/value contract only; native MDL compilation is a Kit experiment."""
    from pxr import Usd, UsdGeom, UsdShade
    from dc.cfd_bridge import render_scene
    p = packet()
    p['primitives'][0]['material'].update(opacity=opacity, alphaTest=.2)
    original = json.dumps(p, sort_keys=True)
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, p, tmp_path)
    prim = stage.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    source = UsdShade.Shader(material.GetPrim().GetChild('Surface'))
    native = UsdShade.Shader(material.GetPrim().GetChild('FractionalSurface'))
    assert native.GetImplementationSource() == 'sourceAsset'
    assert native.GetSourceAssetSubIdentifier('mdl') == 'DcdtuniFractionalPreview'
    assert material.ComputeSurfaceSource('mdl')[0].GetPath() == native.GetPath()
    assert material.ComputeSurfaceSource()[0].GetPath() == source.GetPath()
    for name, value in [('opacity', opacity), ('opacityThreshold', .2), ('roughness', .4), ('metallic', .7)]:
        link = native.GetInput(name).GetConnectedSource()
        assert link[0].GetPrim().GetPath() == material.GetPath()
        assert link[1] == name and link[2] == UsdShade.AttributeType.Input
        for surface in (source, native):
            producing = surface.GetInput(name).GetValueProducingAttributes()
            assert len(producing) == 1 and producing[0].Get() == pytest.approx(value)
    saved = {}
    assert render_scene.override_colours(stage, {str(prim.GetPath()): (.2, .3, .4)}, saved) == 1
    for surface in _bound_surfaces(prim):  # the primitive's painted copy of the look
        producing = surface.GetInput('diffuseColor').GetValueProducingAttributes()
        assert len(producing) == 1 and tuple(producing[0].Get()) == pytest.approx((.2, .3, .4))
    for surface in (source, native):  # the shared look itself is untouched
        producing = surface.GetInput('diffuseColor').GetValueProducingAttributes()
        assert len(producing) == 1 and tuple(producing[0].Get()) == (1, 0, 0)
    render_scene.restore_colours(stage, saved)
    assert [s.GetPath() for s in _bound_surfaces(prim)] == [source.GetPath(), native.GetPath()]
    assert saved == {}
    mdl_asset = native.GetSourceAsset('mdl')
    assert Path(mdl_asset.path).read_bytes() == Path(render_scene.__file__).with_name('DcdtuniFractionalPreview.mdl').read_bytes()
    assert json.dumps(p, sort_keys=True) == original
    assert receipt['primitives'] == 1 and prim.GetCustomDataByKey('dcdtuni:modelUid') == 'rack-1'
    assert list(UsdGeom.Mesh(prim).GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]
    assert [tuple(v) for v in UsdGeom.Mesh(prim).GetPointsAttr().Get()] == [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    assert UsdGeom.Mesh(prim).GetDoubleSidedAttr().Get() is True
    apply_view(stage, p['view'])
    assert json.loads(stage.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:viewJson'))['selectedUids'] == ['rack-1']
    assert render_scene.read_view(stage, receipt['uidPaths']['rack-1'])['selectedUids'] == ['rack-1']
    assert render_scene.read_view(stage, ())['selectedUids'] == []


def _bound_surfaces(prim):
    """(Surface, FractionalSurface) of the material ``prim`` is bound to now."""
    from pxr import UsdShade
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0].GetPrim()
    return [UsdShade.Shader(material.GetChild(n)) for n in ('Surface', 'FractionalSurface')]


def _two_alpha_pixels_png():
    import base64, struct, zlib
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 6, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(b'\0\xff\x80\x00\x40\xff\x80\x00\xff')) + chunk(b'IEND', b'')
    return data, 'data:image/png;base64,' + base64.b64encode(data).decode()


def test_fractional_alpha_map_retains_scalar_texture_and_all_pbr_uv_connections(tmp_path):
    from pxr import Usd, UsdGeom, UsdShade
    from dc.cfd_bridge import render_scene
    data, uri = _two_alpha_pixels_png()
    p = packet()
    p['textures'] = {'color': uri, 'normal': uri, 'rough': uri, 'metal': uri}
    p['primitives'][0]['material'].update(map='color', normalMap='normal', roughnessMap='rough',
        metalnessMap='metal', normalScale=[2, -1], alphaTest=.2, wrapS='mirror', wrapT='clamp')
    original = json.dumps(p, sort_keys=True)
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, p, tmp_path)
    prim = stage.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    source = UsdShade.Shader(material.GetPrim().GetChild('Surface'))
    native = UsdShade.Shader(material.GetPrim().GetChild('FractionalSurface'))
    tex = UsdShade.Shader(material.GetPrim().GetChild('ColorTexture'))
    assert native.GetInput('opacity').GetValueProducingAttributes()[0].GetPath() == tex.GetOutput('a').GetAttr().GetPath()
    assert tuple(tex.GetInput('scale').Get()) == pytest.approx((1, 0, 0, .6))
    assert tuple(tex.GetInput('bias').Get()) == (0, 0, 0, 0)
    assert tex.GetInput('file').Get().path.endswith('.png')
    assert Path(tex.GetInput('file').Get().path).read_bytes() == data
    for name, node, output in [('diffuseColor', 'ColorTexture', 'rgb'), ('normal', 'NormalTexture', 'rgb'),
                               ('roughness', 'roughnessMap', 'g'), ('metallic', 'metalnessMap', 'b')]:
        producing = native.GetInput(name).GetValueProducingAttributes()
        assert len(producing) == 1 and producing[0].GetPath() == material.GetPath().AppendPath(node).AppendProperty('outputs:' + output)
    for node in ('ColorTexture', 'NormalTexture', 'roughnessMap', 'metalnessMap'):
        sampler = UsdShade.Shader(material.GetPrim().GetChild(node))
        assert sampler.GetInput('wrapS').Get() == 'mirror' and sampler.GetInput('wrapT').Get() == 'clamp'
        assert sampler.GetInput('st').GetConnectedSource()[0].GetPrim().GetPath() == material.GetPath().AppendPath('UV')
    saved = {}
    assert render_scene.override_colours(stage, {str(prim.GetPath()): (.2, .3, .4)}, saved) == 1
    for surface in _bound_surfaces(prim):
        own_tex = UsdShade.Shader(surface.GetPrim().GetParent().GetChild('ColorTexture'))
        producing = surface.GetInput('diffuseColor').GetValueProducingAttributes()
        assert len(producing) == 1 and tuple(producing[0].Get()) == pytest.approx((.2, .3, .4))
        assert surface.GetInput('opacity').GetValueProducingAttributes()[0].GetPath() == own_tex.GetOutput('a').GetAttr().GetPath()
    render_scene.restore_colours(stage, saved)
    assert [s.GetPath() for s in _bound_surfaces(prim)] == [source.GetPath(), native.GetPath()]
    for surface in (source, native):
        producing = surface.GetInput('diffuseColor').GetValueProducingAttributes()
        assert len(producing) == 1 and producing[0].GetPath() == tex.GetOutput('rgb').GetAttr().GetPath()
    assert saved == {}
    assert UsdGeom.Mesh(prim).GetDoubleSidedAttr().Get() is True
    apply_view(stage, p['view'])
    assert json.loads(stage.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:viewJson'))['selectedUids'] == ['rack-1']
    assert render_scene.read_view(stage, receipt['uidPaths']['rack-1'])['selectedUids'] == ['rack-1']
    assert render_scene.read_view(stage, ())['selectedUids'] == []
    assert json.dumps(p, sort_keys=True) == original
    # Opaque scalar + genuine alpha-carrying PNG still requires native alpha.
    p['primitives'][0]['material']['opacity'] = 1
    alpha_stage = Usd.Stage.CreateInMemory()
    alpha_receipt = author_scene(alpha_stage, p, tmp_path / 'alpha-only')
    alpha_prim = alpha_stage.GetPrimAtPath(alpha_receipt['uidPaths']['rack-1'][0])
    alpha_material = UsdShade.MaterialBindingAPI(alpha_prim).ComputeBoundMaterial()[0]
    assert alpha_material.GetSurfaceOutput('mdl').HasConnectedSource()


def test_opaque_jpeg_retains_exact_old_usd_graph_without_native_material(tmp_path):
    from pxr import Usd, UsdShade
    p = packet()
    p['primitives'][0]['material'].update(opacity=1, map='color')
    p['textures'] = {'color': JPEG_1PX}
    original = json.dumps(p, sort_keys=True)
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, p, tmp_path)
    prim = stage.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    assert not material.GetSurfaceOutput('mdl')
    assert not material.GetPrim().GetChild('FractionalSurface')
    assert {x.GetName() for x in material.GetPrim().GetChildren()} == {'Surface', 'UV', 'ColorTexture'}
    source = UsdShade.Shader(material.GetPrim().GetChild('Surface'))
    assert source.GetIdAttr().Get() == 'UsdPreviewSurface'
    assert material.ComputeSurfaceSource()[0].GetPath() == source.GetPath()
    tex = UsdShade.Shader(material.GetPrim().GetChild('ColorTexture'))
    assert tuple(tex.GetInput('scale').Get()) == (1, 0, 0, 1)
    assert tex.GetInput('sourceColorSpace').Get() == 'sRGB'
    # The scene-owned tiny module supports later translucent view updates;
    # the opaque shader graph above remains unchanged.
    from dc.cfd_bridge import render_scene
    import hashlib
    jpeg, _ = render_scene.texture_bytes(JPEG_1PX)
    mdl = Path(render_scene.__file__).with_name('DcdtuniFractionalPreview.mdl').read_bytes()
    assert {f.name for f in tmp_path.iterdir()} == {hashlib.sha256(jpeg).hexdigest() + '.jpg',
        'DcdtuniFractionalPreview_' + hashlib.sha256(mdl).hexdigest()[:16] + '.mdl'}
    assert json.dumps(p, sort_keys=True) == original


def _fractional_grid():
    return {'visible': True, 'spacingM': .6, 'divisions': 4, 'extentM': 2.4,
            'centerColor': [.2, .3, .4], 'lineColor': [.1, .2, .3], 'opacity': .2,
            'matrix': [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., -.005, 0., 1.]}


@pytest.mark.parametrize('scene_opacity', [.6, 1.0])
def test_fractional_background_grid_uses_scene_asset_through_author_and_view_updates(tmp_path, scene_opacity):
    from pxr import Usd, UsdGeom, UsdShade
    from dc.cfd_bridge import render_scene
    p = packet()
    p['primitives'][0]['material']['opacity'] = scene_opacity
    # A pure opaque scene can start with an opaque grid, then become fractional.
    p['view']['backgroundGrid'] = _fractional_grid()
    if scene_opacity == 1.0:
        p['view']['backgroundGrid']['opacity'] = 1.0
    original = json.dumps(p, sort_keys=True)
    stage = Usd.Stage.CreateInMemory()
    receipt = author_scene(stage, p, tmp_path)
    scene_asset = stage.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:fractionalMaterialAsset')
    assert Path(scene_asset).read_bytes() == Path(render_scene.__file__).with_name('DcdtuniFractionalPreview.mdl').read_bytes()
    mesh = stage.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
    mesh_material = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    if scene_opacity == 1.0:
        assert not mesh_material.GetSurfaceOutput('mdl') and not mesh_material.GetPrim().GetChild('FractionalSurface')
    before_points = list(UsdGeom.Mesh(mesh).GetPointsAttr().Get())
    view = copy.deepcopy(p['view'])
    view['backgroundGrid']['opacity'] = .2
    apply_view(stage, view)
    grid = stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT + '/Lines')
    material = UsdShade.MaterialBindingAPI(grid).ComputeBoundMaterial()[0]
    native = UsdShade.Shader(material.GetPrim().GetChild('FractionalSurface'))
    assert native.GetSourceAsset('mdl').path == scene_asset
    producing = native.GetInput('opacity').GetValueProducingAttributes()
    assert len(producing) == 1 and producing[0].Get() == pytest.approx(.2)
    points = list(UsdGeom.BasisCurves(grid).GetPointsAttr().Get())
    assert UsdShade.Shader(material.GetPrim().GetChild('VertexColor')).GetIdAttr().Get() == 'UsdPrimvarReader_float3'
    view['backgroundGrid']['opacity'] = .4
    apply_view(stage, view)
    material = UsdShade.Material(stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT + '/Looks/Grid'))
    native = UsdShade.Shader(material.GetPrim().GetChild('FractionalSurface'))
    assert native.GetSourceAsset('mdl').path == scene_asset
    assert native.GetInput('opacity').GetValueProducingAttributes()[0].Get() == pytest.approx(.4)
    assert list(UsdGeom.BasisCurves(stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT + '/Lines')).GetPointsAttr().Get()) == points
    view['backgroundGrid']['visible'] = False
    apply_view(stage, view)
    assert UsdGeom.Imageable(stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT)).GetVisibilityAttr().Get() == 'invisible'
    assert not stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT + '/Lines')
    view['backgroundGrid']['visible'] = True
    apply_view(stage, view)
    native = UsdShade.Shader(stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT + '/Looks/Grid/FractionalSurface'))
    assert native.GetSourceAsset('mdl').path == scene_asset
    assert list(UsdGeom.Mesh(mesh).GetPointsAttr().Get()) == before_points
    assert mesh.GetCustomDataByKey('dcdtuni:modelUid') == 'rack-1'
    assert render_scene.read_view(stage, receipt['uidPaths']['rack-1'])['selectedUids'] == ['rack-1']
    assert json.dumps(p, sort_keys=True) == original


def test_fractional_grid_public_view_on_standalone_scene_uses_tracked_source(tmp_path):
    from pxr import Usd, UsdGeom, UsdShade
    from dc.cfd_bridge import render_scene
    stage = Usd.Stage.CreateInMemory()
    view = {'selectedUids': [], 'timeSeconds': None, 'backgroundGrid': _fractional_grid()}
    # Existing no-editor refusal is retained; the legal standalone root works.
    with pytest.raises(ValueError, match='No editor render scene loaded'):
        apply_view(stage, view)
    UsdGeom.Xform.Define(stage, render_scene.ROOT)
    apply_view(stage, view)
    native = UsdShade.Shader(stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT + '/Looks/Grid/FractionalSurface'))
    assert Path(native.GetSourceAsset('mdl').path) == Path(render_scene.__file__).with_name('DcdtuniFractionalPreview.mdl')
    assert native.GetInput('opacity').GetValueProducingAttributes()[0].Get() == pytest.approx(.2)
    assert not stage.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:fractionalMaterialAsset')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('scene_owned', [False, True])
def test_ribbon_carriers_are_opaque_strips_carrying_the_blend_over_the_backdrop(tmp_path, native, scene_owned):
    """A 1-px fractional strip was dithered away by RTX's temporal AA and the
    video encoder; the strip is opaque and its colour is Three's alpha blend
    of the line over Kit's constant backdrop."""
    from pxr import Usd, UsdGeom, UsdShade
    from dc.cfd_bridge import render_scene as r, grid_raster as g, grid_viewport as v
    stage = Usd.Stage.CreateInMemory()
    p = packet()
    original = json.dumps(p, sort_keys=True)
    if scene_owned:
        receipt = author_scene(stage, p, tmp_path / 'assets', asset_prefix='assets')
        expected_asset = stage.GetPrimAtPath(r.ROOT).GetCustomDataByKey('dcdtuni:fractionalMaterialAsset')
        assert expected_asset.startswith('assets/') and not Path(expected_asset).is_absolute()
        model = stage.GetPrimAtPath(receipt['uidPaths']['rack-1'][0])
        model_points = list(UsdGeom.Mesh(model).GetPointsAttr().Get())
    else:
        UsdGeom.Xform.Define(stage, r.ROOT)
        expected_asset = str(Path(r.__file__).with_name('DcdtuniFractionalPreview.mdl'))
    grid = _fractional_grid()
    data = {'points': [(-1., 0., -.01), (1., 0., -.01), (1., 0., .01), (-1., 0., .01)],
            'colors': [(.1, .2, .3)] * 4, 'counts': [4], 'sourceLines': [0]}
    observation = None
    if native:
        # Typed controlled viewport identity; not a Kit or encoded pixel proof.
        observation = v.NativeGridObservation(object(), object(), ('ControlledViewport', '', '/Render/Controlled', ('time', 0.)),
            'controlled-context', 'controlled-view', r._background_grid_intent_json(grid), 3,
            tuple(grid['matrix']), (1920, 1080), (1920, 1080), 4)
        data.update(widthDomain='native-viewport-image-pixel', encodedFrameProof='unresolved', gridPixelVerified=False)
    key = 'controlled-fractional-ribbon'
    contract = g.author_ribbons(stage, grid, data, key, _native_observation=observation)
    matches = g.native_carrier_matches if native else g.carrier_matches
    assert matches(stage, key, contract) is True
    assert type(contract) is (g.NativeCarrierContract if native else g.CarrierContract)
    stroke = UsdGeom.Mesh(stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT + '/Stroke'))
    assert list(stroke.GetFaceVertexCountsAttr().Get()) == data['counts']
    assert list(stroke.GetFaceVertexIndicesAttr().Get()) == [0, 1, 2, 3]
    for actual, expected in zip(stroke.GetPointsAttr().Get(), data['points']):
        assert tuple(actual) == pytest.approx(expected)
    assert stroke.GetDoubleSidedAttr().Get() is True
    material = UsdShade.MaterialBindingAPI(stroke.GetPrim()).ComputeBoundMaterial()[0]
    assert not material.GetPrim().GetChild('FractionalSurface')  # no dithered cutout
    assert not material.GetPrim().GetChild('VertexColor')  # a constant emission, no primvar reader
    shader = UsdShade.Shader(material.GetPrim().GetChild('Surface'))
    assert shader.GetInput('opacity').Get() == pytest.approx(1)
    assert tuple(shader.GetInput('diffuseColor').Get()) == (0, 0, 0)
    assert tuple(shader.GetInput('specularColor').Get()) == (0, 0, 0) and shader.GetInput('useSpecularWorkflow').Get() == 1
    alpha, backdrop = grid['opacity'], r.backdrop_linear()
    blended = tuple(g.GRID_EMISSION_GAIN * (alpha * c + (1 - alpha) * b) for c, b in zip((.1, .2, .3), backdrop))
    assert tuple(shader.GetInput('emissiveColor').Get()) == pytest.approx(blended, abs=1e-6)
    if native:
        assert contract.observation is observation
        assert data['encodedFrameProof'] == 'unresolved' and data['gridPixelVerified'] is False
    if scene_owned:
        assert list(UsdGeom.Mesh(model).GetPointsAttr().Get()) == model_points
        assert model.GetCustomDataByKey('dcdtuni:modelUid') == 'rack-1'
    else:
        assert not stage.GetPrimAtPath(r.ROOT).GetCustomDataByKey('dcdtuni:fractionalMaterialAsset')
        assert list(tmp_path.iterdir()) == []
    assert json.dumps(p, sort_keys=True) == original


@pytest.mark.parametrize('eye, up, faces_up', [((0, 10, .001), (0, 0, -1), True), ((8, 6, 8), (0, 1, 0), True),
                                               ((3, -5, 3), (0, 1, 0), False)])
def test_native_grid_strips_face_the_camera(eye, up, faces_up):
    """RTX emits an opaque surface from its front side only; a strip seen from
    behind rendered black, so every strip is wound towards the camera."""
    import math
    from pxr import Gf
    from dc.cfd_bridge import grid_raster as g, native_grid_geometry as ng
    grid = dict(_fractional_grid(), divisions=20, extentM=12.0)
    points, colors = g.canonical_grid(grid)
    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(0, 0, 0), Gf.Vec3d(*up))
    f, near, far = 1 / math.tan(math.radians(22.5)), .1, 400
    projection = Gf.Matrix4d(f * 9 / 16, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1,
                             0, 0, 2 * far * near / (near - far), 0)
    m = view * projection
    out = ng.native_grid_strips(grid, points, colors, [m[i][j] for i in range(4) for j in range(4)], (1600, 900), 1.5)
    assert out['counts']
    start = 0
    for count in out['counts']:
        strip = out['points'][start:start + count]
        start += count
        normal_y = sum((a[2] - b[2]) * (a[0] + b[0]) for a, b in zip(strip, strip[1:] + strip[:1]))
        assert (normal_y > 0) is faces_up


def test_fractional_native_ribbon_still_rejects_untyped_observation_before_stage_edit():
    from pxr import Usd, UsdGeom
    from dc.cfd_bridge import render_scene as r, grid_raster as g
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, r.ROOT)
    before = stage.GetRootLayer().ExportToString()
    data = {'points': [(0., 0., 0.)], 'colors': [(1., 1., 1.)], 'counts': [1],
            'widthDomain': 'native-viewport-image-pixel', 'encodedFrameProof': 'unresolved', 'gridPixelVerified': False}
    with pytest.raises(g.GridRefused, match='native grid domain is not an encoded receipt'):
        g.author_ribbons(stage, _fractional_grid(), data, 'controlled-invalid', _native_observation=object())
    assert stage.GetRootLayer().ExportToString() == before


def test_view_lighting_scales_the_rig_and_sets_rtx_shadows(tmp_path):
    """The editor's lighting (view.lighting) reaches Kit: the preset and scale on
    dome/key/fill, shadows and exposure compensation on RTX, from materials.json."""
    from pxr import Usd, UsdLux
    from dc.cfd_bridge import render_scene
    from dc.cfd_bridge.material_library import kit_lighting, lighting
    stage = Usd.Stage.CreateInMemory()
    author_scene(stage, packet(), tmp_path)

    class Settings(dict):
        def set(self, key, value):
            self[key] = value
    settings = Settings()
    rig = kit_lighting()
    bright = lighting()['presets']['bright']
    targets = render_scene.apply_lighting(
        stage, {'enabled': True, 'scale': 0.5, 'preset': 'bright', 'shadows': False}, settings)
    intensity = lambda name: UsdLux.LightAPI(stage.GetPrimAtPath('/World/RenderScene/Lighting/' + name)).GetIntensityAttr().Get()  # noqa: E731
    assert intensity('Key') == pytest.approx(rig['keyIntensity'] * bright['kitKey'] * 0.5)
    assert intensity('Fill') == pytest.approx(rig['fillIntensity'] * bright['kitFill'] * 0.5)
    assert intensity('Dome') == pytest.approx(rig['domeIntensity'] * bright['kitDome'] * 0.5)
    assert settings['/rtx/shadows/enabled'] is False
    assert settings['/rtx/post/tonemap/exposureCompensation'] == pytest.approx(rig['exposureCompensation'] + bright['kitExposureStops'])
    assert targets['shadows'] is False

    assert intensity('Shadow') == 0  # shadows off: the shadow light is dark

    # Lights off: the directional pair goes dark, the dome carries the scene.
    render_scene.apply_lighting(stage, {'enabled': False, 'scale': 1, 'preset': 'standard', 'shadows': True}, settings)
    assert intensity('Key') == 0 and intensity('Fill') == 0 and intensity('Shadow') == 0
    assert intensity('Dome') == pytest.approx(rig['domeIntensity'] * render_scene.LIGHTS_OFF_DOME)
    assert settings['/rtx/shadows/enabled'] is True

    # Through the view (validated against the render-scene schema). Shadows on:
    # the shadow light takes over its share of the key, as in the editor, and
    # is the only light that casts.
    share = lighting()['shadow']['share']
    view = dict(packet()['view'], lighting={'enabled': True, 'scale': 1, 'preset': 'standard', 'shadows': True})
    apply_view(stage, view)
    assert intensity('Key') == pytest.approx(rig['keyIntensity'] * (1 - share))
    assert intensity('Shadow') == pytest.approx(rig['keyIntensity'] * share)
    casts = {name: UsdLux.ShadowAPI(stage.GetPrimAtPath('/World/RenderScene/Lighting/' + name)).GetShadowEnableAttr().Get()
             for name in ('Dome', 'Key', 'Fill', 'Shadow')}
    assert casts == {'Dome': False, 'Key': False, 'Fill': False, 'Shadow': True}
    with pytest.raises(Exception):
        apply_view(stage, dict(view, lighting={'enabled': True, 'scale': 9, 'preset': 'standard', 'shadows': True}))
