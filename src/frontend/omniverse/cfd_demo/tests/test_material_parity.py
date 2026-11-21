"""One kind, one material: the editor snapshot, the Python reader and both Kit paths.

The golden src/frontend/react/test/fixtures/renderMaterialByKind.json is made by
the real editor (test/renderMaterialParity.test.ts): for each case one face is
built by geometry_scene.js and snapshotted, texture fields resolved to their
pool references. Here:

1. dc.cfd_bridge.material_library.render_material / texture_uv must give the
   same material and UVs for the same case (the Python mirror of the colour,
   roughness and UV rules);
2. the Kit-native .cfdproj path (usdgen.material_for) and the display bridge
   (render_scene.author_scene fed the editor's material) must author the same
   UsdPreviewSurface graph for the kind, bound to the same pool files.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'kit/extensions/dc.cfd_bridge'))
from dc.cfd_bridge import material_library as lib  # noqa: E402

REPO = Path(__file__).resolve().parents[5]
GOLDEN_PATH = REPO / 'src/frontend/react/test/fixtures/renderMaterialByKind.json'
GOLDEN = json.loads(GOLDEN_PATH.read_text())
CASES = GOLDEN['cases']
TEXTURE_KEYS = ('map', 'normalMap', 'roughnessMap', 'metalnessMap')


def test_golden_was_made_from_this_manifest():
    sha = hashlib.sha256(lib.MANIFEST_PATH.read_bytes()).hexdigest()
    assert GOLDEN['manifestSha256'] == sha, (
        'textures/materials.json changed since the golden was made: in src/frontend/react run '
        'UPDATE_RENDER_MATERIAL_GOLDEN=1 npx vitest run test/renderMaterialParity.test.ts')


def python_material(case):
    o = case['options']
    picture = lib.pool_ref_for_url(case['picture']) if case.get('picture') else None
    return lib.render_material(case['kind'], color=o.get('color'), keep_user_color=bool(o.get('keepUserColor')),
                               opacity=o.get('opacity', 1), double_sided=bool(o.get('doubleSide')), picture=picture)


def assert_same_material(actual, expected, label):
    assert set(actual) == set(expected), f'{label}: fields {sorted(set(actual) ^ set(expected))}'
    for key, value in expected.items():
        if isinstance(value, list):
            assert actual[key] == pytest.approx(value, abs=1e-12), f'{label}.{key}'
        elif isinstance(value, float):
            assert actual[key] == pytest.approx(value, abs=1e-12), f'{label}.{key}'
        else:
            assert actual[key] == value, f'{label}.{key}'


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_python_reader_gives_the_editor_material(case):
    assert_same_material(python_material(case), case['material'], case['name'])


def python_uvs(case):
    o = case['options']
    norm = o.get('uvNormalize') or {}
    mode = {'stretch': 'stretch', 'tile-per-u': 'tile-per-u', 'tile-physical': 'physical'}.get(norm.get('type'))
    p = case['positions']
    metres = [(p[i], p[i + 1]) for i in range(0, len(p), 3)]   # face frame = world X/Y here
    return lib.texture_uv(case['kind'], metres, u_count=norm.get('uCount', 1), mode=mode,
                          tile_m=norm.get('tileSize'))


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_python_uv_modes_give_the_editor_uvs(case):
    uvs = [c for uv in python_uvs(case) for c in uv]
    # Library-role kinds scale the UVs inside a Float32 buffer (pbr_materials
    # scaleUv); the others multiply float32 UVs by the texture matrix in double.
    assert uvs == pytest.approx(case['uvs'], abs=1e-6), case['name']


# ── the two Kit paths author the same surface ────────────────────────────────

pxr = pytest.importorskip('pxr')
from pxr import Sdf, Usd, UsdShade  # noqa: E402
from dc.cfd_bridge import render_scene, usdgen  # noqa: E402


def _value(inp):
    if inp.HasConnectedSource():
        source, output, _ = inp.GetConnectedSource()
        prim = source.GetPrim()
        # Material interface inputs (the fractional adapter's shared inputs)
        # are named after the material prim, which differs between the paths.
        return ('->', 'Material' if prim.IsA(UsdShade.Material) else prim.GetName(), output)
    value = inp.Get()
    if isinstance(value, Sdf.AssetPath):
        return str(Path(value.resolvedPath or value.path).resolve())
    if hasattr(value, '__len__') and not isinstance(value, str):
        return tuple(round(float(v), 6) for v in value)
    if isinstance(value, float):
        return round(value, 6)
    return value


def surface_graph(stage, material_path):
    """Every input of the material, its UsdPreviewSurface and its textures,
    connections reduced to (source, output) and assets to resolved files."""
    material = stage.GetPrimAtPath(material_path)
    out = {'Material': {inp.GetBaseName(): _value(inp) for inp in UsdShade.Material(material).GetInputs()}}
    for shader in material.GetChildren():
        s = UsdShade.Shader(shader)
        if not s or shader.GetName() == 'UV':
            continue
        if shader.GetName() == 'FractionalSurface':
            asset = Path(s.GetSourceAsset('mdl').path).name
            out['FractionalSurface'] = re.sub(r'(_[0-9a-f]{16})?\.mdl$', '', asset)
            continue
        out[shader.GetName()] = {inp.GetBaseName(): _value(inp) for inp in s.GetInputs()
                                 if inp.GetBaseName() != 'st'}
    return out


def bridge_material(case, tmp_path):
    """Path B: the display bridge authors the editor's own snapshot material."""
    material, textures = dict(case['material']), {}
    for key in TEXTURE_KEYS:
        if key in material:
            textures[key] = material[key]
            material[key] = key
    packet = {
        'schema': 'dcdtuni.render-scene/v2', 'project': {'uid': 'parity', 'name': 'parity'},
        'coordinateSystem': {'upAxis': 'Y', 'metersPerUnit': 1, 'handedness': 'right'},
        'primitives': [{'id': 'p/0', 'modelUid': 'm', 'name': case['name'], 'kind': 'triangles',
                        'positions': case['positions'], 'indices': [0, 1, 2, 0, 2, 3], 'uvs': case['uvs'],
                        'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], 'visible': True,
                        'material': material}],
        'textures': textures, 'view': {'selectedUids': [], 'timeSeconds': None}, 'warnings': [],
    }
    stage = Usd.Stage.CreateInMemory()
    render_scene.author_scene(stage, packet, tmp_path / 'cache')
    looks = stage.GetPrimAtPath(render_scene.ROOT + '/Looks').GetChildren()
    return surface_graph(stage, looks[0].GetPath())


def native_material(case):
    """Path A: the Kit-native .cfdproj path asks usdgen for the same kind."""
    o = case['options']
    stage = Usd.Stage.CreateInMemory()
    path = usdgen.material_for(stage, case['kind'], color=o.get('color'), opacity=o.get('opacity', 1),
                               keep_user_color=bool(o.get('keepUserColor')),
                               double_sided=bool(o.get('doubleSide')), picture=case.get('picture'))
    return surface_graph(stage, path)


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_native_and_bridge_paths_author_the_same_surface(case, tmp_path):
    bridge = bridge_material(case, tmp_path)
    native = native_material(case)
    # Fractional adapters are published per scene (bridge) or used from the
    # tracked source (native); the adapter itself is the same MDL.
    assert native == bridge, case['name']
    for name, values in native.items():
        if isinstance(values, dict) and 'file' in values:
            assert values['file'].startswith(str(lib.POOL_ROOT)), f'{case["name"]} {name} not bound in the pool'
