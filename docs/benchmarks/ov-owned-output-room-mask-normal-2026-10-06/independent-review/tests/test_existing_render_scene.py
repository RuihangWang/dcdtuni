"""CPU-only contract and actual OpenUSD authoring tests; no Kit/RTX imports."""
import copy
import json
import os
import sys
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(os.environ['OV_PRIVATE_TREE']) / 'src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge'))
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
    assert surface.GetInput('opacity').Get() == pytest.approx(.6)
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1
    assert UsdGeom.GetStageUpAxis(stage) == 'Y'
    assert stage.GetPrimAtPath('/World/RenderScene').GetCustomDataByKey('dcdtuni:timeSeconds') == 12.5


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
    roughness = surface.GetInput('roughness').GetConnectedSource()
    metalness = surface.GetInput('metallic').GetConnectedSource()
    assert roughness[1] == 'g'
    assert metalness[1] == 'b'
    assert tuple(UsdShade.Shader(roughness[0]).GetInput('scale').Get()) == pytest.approx((.4, .4, .4, 1))
    assert tuple(UsdShade.Shader(metalness[0]).GetInput('scale').Get()) == pytest.approx((.7, .7, .7, 1))


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
    assert [f.suffix for f in tmp_path.iterdir()] == ['.jpg']
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


def test_colour_override_is_per_primitive_and_restores_connections(tmp_path):
    """Liquid result colouring paints each primitive's own material and undoes
    exactly what it changed, including a vertex-colour connection."""
    from pxr import Gf, Usd
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
    p['primitives'] += [pipe, valve, unlit]
    author_scene(stage, p, tmp_path)
    index = index_render_prims(stage)
    assert set(index) == {('rack-1', '', ''), ('pipe-1', '', ''), ('pipe-1', 'valve-7', ''), ('label-1', '', '')}

    def attr(path, name):
        leaf = path.rsplit('/', 1)[-1]
        return stage.GetAttributeAtPath(f'/World/RenderScene/Looks/{leaf}/Surface.inputs:{name}')

    tube = index[('pipe-1', '', '')][0]
    fitting = index[('pipe-1', 'valve-7', '')][0]
    label = index[('label-1', '', '')][0]
    rack = index[('rack-1', '', '')][0]
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
    override_colours(stage, {tube: (0, 1, 0)}, saved)  # repaint keeps the FIRST original
    restore_colours(stage, saved)
    assert saved == {}
    assert tuple(attr(tube, 'diffuseColor').Get()) == pytest.approx((.5, .5, .5))
    assert tuple(attr(tube, 'emissiveColor').Get()) == pytest.approx(tube_emissive_before)
    assert attr(fitting, 'diffuseColor').GetConnections()
    assert tuple(attr(label, 'emissiveColor').Get()) == pytest.approx((.2, .3, .4))


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
