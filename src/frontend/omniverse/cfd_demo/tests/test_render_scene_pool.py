"""render-scene v2 on the Kit side: pool references bind the pool's own files.

CPU-only OpenUSD (pxr), no Kit/RTX. The React snapshot sends
`pool:<file>#<sha256>` for a texture that is a pool file; Kit must bind that
very file (absolute in Kit, relative to the USD file in a disk export) and
write nothing of its own for it.
"""
import copy
import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'kit/extensions/dc.cfd_bridge'))
from dc.cfd_bridge import material_library as lib  # noqa: E402
from dc.cfd_bridge.render_scene import (author_scene, kit_exposure_settings, retire_foreign_lights,  # noqa: E402
                                        validate_snapshot)

pxr = pytest.importorskip('pxr')
from pxr import Usd, UsdGeom, UsdLux, UsdShade  # noqa: E402

POOL = Path(lib.POOL_ROOT)


def packet(textures, material_extra, schema='dcdtuni.render-scene/v2'):
    material = {'color': [1, 1, 1], 'emissive': [0, 0, 0], 'opacity': 1, 'roughness': .5,
                'metalness': 0, 'doubleSided': True, 'unlit': False, **material_extra}
    return {
        'schema': schema,
        'project': {'uid': 'pool-project', 'name': 'pool'},
        'coordinateSystem': {'upAxis': 'Y', 'metersPerUnit': 1, 'handedness': 'right'},
        'primitives': [{
            'id': 'rack/0', 'modelUid': 'rack-1', 'name': 'rack', 'kind': 'triangles',
            'positions': [0, 0, 0, 1, 0, 0, 0, 1, 0], 'indices': [0, 1, 2], 'uvs': [0, 0, 1, 0, 0, 1],
            'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], 'visible': True,
            'material': material,
        }],
        'textures': textures, 'view': {'selectedUids': [], 'timeSeconds': None}, 'warnings': [],
    }


def steel_packet():
    refs = {k: lib.pool_ref(f'painted-steel/{k}.jpg') for k in ('color', 'normal', 'roughness')}
    return packet({'c': refs['color'], 'n': refs['normal'], 'r': refs['roughness']},
                  {'map': 'c', 'normalMap': 'n', 'roughnessMap': 'r', 'normalScale': [.2, .2],
                   'mapColorSpace': 'srgb', 'normalColorSpace': 'linear', 'wrapS': 'repeat', 'wrapT': 'repeat'}), refs


def shader_file(stage, name):
    material = stage.GetPrimAtPath('/World/RenderScene/Looks').GetChildren()[0]
    shader = UsdShade.Shader(material.GetChild(name))
    return shader.GetInput('file').Get()


def test_v2_pool_refs_validate_and_v1_may_not_carry_them():
    p, _ = steel_packet()
    validate_snapshot(p)
    v1 = copy.deepcopy(p)
    v1['schema'] = 'dcdtuni.render-scene/v1'
    with pytest.raises(ValueError, match='invalid string'):
        validate_snapshot(v1)


def test_a_ref_from_another_manifest_is_refused_before_any_stage_edit(tmp_path):
    p, refs = steel_packet()
    p['textures']['c'] = refs['color'][:-64] + 'e' * 64
    with pytest.raises(ValueError, match='differs between the sender and this checkout'):
        validate_snapshot(p)
    stage = Usd.Stage.CreateInMemory()
    with pytest.raises(ValueError, match='differs between the sender'):
        author_scene(stage, p, tmp_path / 'cache')
    assert not stage.GetPrimAtPath('/World/RenderScene')


def test_kit_binds_the_pool_file_itself_and_writes_no_copy(tmp_path):
    p, refs = steel_packet()
    stage = Usd.Stage.CreateInMemory()
    cache = tmp_path / 'render-textures'
    author_scene(stage, p, cache)
    for name, key in (('ColorTexture', 'color'), ('NormalTexture', 'normal'), ('roughnessMap', 'roughness')):
        asset = shader_file(stage, name)
        expected = POOL / f'painted-steel/{key}.jpg'
        assert asset.path == str(expected)
        sha = refs[key].rsplit('#', 1)[1]
        assert hashlib.sha256(Path(asset.path).read_bytes()).hexdigest() == sha
    # Only the tracked fractional-material adapter is published; no texture bytes.
    assert sorted(f.suffix for f in cache.iterdir()) == ['.mdl']


def test_alpha_in_a_pool_png_selects_the_fractional_material(tmp_path):
    ref = lib.pool_ref('containment-panel/color.png')
    p = packet({'c': ref}, {'map': 'c', 'mapColorSpace': 'srgb', 'wrapS': 'repeat', 'wrapT': 'repeat'})
    stage = Usd.Stage.CreateInMemory()
    author_scene(stage, p, tmp_path / 'cache')
    material = stage.GetPrimAtPath('/World/RenderScene/Looks').GetChildren()[0]
    assert material.GetChild('FractionalSurface')
    assert shader_file(stage, 'ColorTexture').path == str(POOL / 'containment-panel/color.png')


def test_a_disk_export_names_the_pool_file_relative_to_the_usd(tmp_path):
    p, _ = steel_packet()
    out_dir = POOL.parents[3] / '_tmp' / 'campaign_M' / 'pytest' / 'pool_export'
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateInMemory()
    author_scene(stage, p, out_dir / 'scene_textures', asset_prefix='scene_textures')
    usd = out_dir / 'scene.usda'
    stage.GetRootLayer().Export(str(usd))
    reopened = Usd.Stage.Open(str(usd))
    asset = shader_file(reopened, 'ColorTexture')
    assert not os.path.isabs(asset.path)
    assert Path(asset.resolvedPath).resolve() == (POOL / 'painted-steel/color.jpg').resolve()
    # still no copy beside the export
    assert not list((out_dir / 'scene_textures').glob('*.jpg'))


def test_data_uri_textures_still_land_in_the_owned_cache(tmp_path):
    png = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM1kAAAAASUVORK5CYII=')
    p = packet({'c': png}, {'map': 'c'})
    stage = Usd.Stage.CreateInMemory()
    cache = tmp_path / 'cache'
    author_scene(stage, p, cache)
    asset = shader_file(stage, 'ColorTexture')
    assert Path(asset.path).parent == cache.resolve() and asset.path.endswith('.png')


def test_the_editor_scene_is_lit_by_the_manifest_rig(tmp_path):
    p, _ = steel_packet()
    stage = Usd.Stage.CreateInMemory()
    author_scene(stage, p, tmp_path / 'cache')
    kit = lib.kit_lighting()
    dome = UsdLux.DomeLight(stage.GetPrimAtPath('/World/RenderScene/Lighting/Dome'))
    key = UsdLux.DistantLight(stage.GetPrimAtPath('/World/RenderScene/Lighting/Key'))
    fill = UsdLux.DistantLight(stage.GetPrimAtPath('/World/RenderScene/Lighting/Fill'))
    assert dome.GetIntensityAttr().Get() == pytest.approx(kit['domeIntensity'])
    assert key.GetIntensityAttr().Get() == pytest.approx(kit['keyIntensity'])
    assert fill.GetIntensityAttr().Get() == pytest.approx(kit['fillIntensity'])
    assert tuple(key.GetColorAttr().Get()) == pytest.approx(tuple(lib.srgb_to_linear(kit['keyColor'])), abs=1e-6)
    assert tuple(dome.GetColorAttr().Get()) == pytest.approx(tuple(lib.srgb_to_linear(kit['domeColor'])), abs=1e-6)
    rotate = key.GetPrim().GetAttribute('xformOp:rotateXYZ').Get()
    assert tuple(rotate) == pytest.approx(tuple(kit['keyRotateXYZ']))


def _with_kit(monkeypatch, **changes):
    lighting = copy.deepcopy(lib.lighting())
    lighting['kit'].update(changes)
    monkeypatch.setattr(lib, 'lighting', lambda: lighting)
    monkeypatch.setattr(lib, 'kit_lighting', lambda: lighting['kit'])
    return lighting


def test_a_fixed_exposure_is_the_manifest_camera(monkeypatch):
    # A fixed RTX camera gives an object the same brightness in every view, as
    # the editors' fixed toneMappingExposure does; the histogram re-meters each
    # frame, so a dark backdrop brightened the whole scene.
    lighting = _with_kit(monkeypatch, autoExposure=False,
                         fixedExposure={'filmIso': 200, 'exposureTime': 0.01, 'fNumber': 4})
    settings = dict(kit_exposure_settings())
    assert settings['/rtx/post/histogram/enabled'] is False
    assert settings['/rtx/post/tonemap/filmIso'] == 200
    assert settings['/rtx/post/tonemap/exposureTime'] == 0.01
    assert settings['/rtx/post/tonemap/fNumber'] == 4
    assert settings['/rtx/shadows/enabled'] is lighting['kit']['shadows']


def test_the_backdrop_is_the_editors_viewport_colour(monkeypatch):
    # A constant colour, as the editors' viewport: the dome seen as backdrop read
    # as a grey sky with a denoised horizon behind the room.
    _with_kit(monkeypatch, background='#141b25')
    settings = dict(kit_exposure_settings())
    assert settings['/rtx/background/source/type'] == 2
    assert settings['/rtx/background/source/color'] == pytest.approx(lib.srgb_to_linear('#141b25'))


def test_pinned_rtx_settings_follow_the_manifest(monkeypatch):
    _with_kit(monkeypatch, rtxSettings={'/rtx/post/backgroundZeroAlpha/postTonemapBackgroundComposite': False})
    settings = dict(kit_exposure_settings())
    assert settings['/rtx/post/backgroundZeroAlpha/postTonemapBackgroundComposite'] is False


def test_the_tone_mapper_is_the_editors(monkeypatch):
    _with_kit(monkeypatch)
    assert dict(kit_exposure_settings())['/rtx/post/tonemap/op'] == 6  # RTX "Aces" = three ACESFilmic


def test_auto_exposure_leaves_the_camera_alone(monkeypatch):
    _with_kit(monkeypatch, autoExposure=True, exposureCompensation=-1.0)
    settings = dict(kit_exposure_settings())
    assert settings['/rtx/post/histogram/enabled'] is True
    assert settings['/rtx/post/tonemap/exposureCompensation'] == -1.0
    assert '/rtx/post/tonemap/filmIso' not in settings


def test_the_manifest_rig_uses_a_fixed_camera():
    kit = lib.kit_lighting()
    assert kit['autoExposure'] is False
    assert set(kit['fixedExposure']) == {'filmIso', 'exposureTime', 'fNumber'}


def _template_light(stage):
    """What Kit's default new-stage template ("sunlight") leaves on every new stage:
    /Environment/defaultLight, a 3000-intensity DistantLight with no rotation, which
    on this Y-up stage shines horizontally along -Z."""
    UsdGeom.Xform.Define(stage, '/Environment')
    light = UsdLux.DistantLight.Define(stage, '/Environment/defaultLight')
    light.CreateIntensityAttr(3000.0)
    return light


def _active_lights(stage):
    return sorted(str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdLux.LightAPI))


def test_the_manifest_rig_is_the_only_light_on_an_editor_scene(tmp_path):
    p, _ = steel_packet()
    stage = Usd.Stage.CreateInMemory()
    _template_light(stage)
    author_scene(stage, p, tmp_path / 'cache')
    assert not stage.GetPrimAtPath('/Environment/defaultLight').IsActive()
    assert _active_lights(stage) == ['/World/RenderScene/Lighting/Dome', '/World/RenderScene/Lighting/Fill',
                                     '/World/RenderScene/Lighting/Key', '/World/RenderScene/Lighting/Shadow']


def test_retire_foreign_lights_keeps_the_named_rig():
    stage = Usd.Stage.CreateInMemory()
    _template_light(stage)
    UsdLux.DomeLight.Define(stage, '/World/Env/DomeLight')
    retired = retire_foreign_lights(stage, '/World/Env')
    assert retired == ['/Environment/defaultLight']
    assert _active_lights(stage) == ['/World/Env/DomeLight']


def test_the_scene_reports_the_texture_files_it_bound(tmp_path):
    # The receipt Kit logs after a load: the very files the stage binds.
    p, refs = steel_packet()
    result = author_scene(Usd.Stage.CreateInMemory(), p, tmp_path / 'cache')
    expected = sorted(str(POOL / ref.split(':', 1)[1].split('#', 1)[0]) for ref in refs.values())
    assert result['textureAssets'] == expected
