"""The Kit-side reader of the shared material pool (no Kit, no pxr, no GPU).

textures/materials.json is read by js/material_library.js for both editors and
by dc.cfd_bridge.material_library for Kit. These tests pin the Python reader to
the same file and the same colour/roughness rules, and pin how a pool
reference (`pool:<file>#<sha256>`) resolves: to the pool's own file, after the
manifest, the checkout boundary and the bytes all agree.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

EXTENSION = Path(__file__).resolve().parents[1] / 'kit/extensions/dc.cfd_bridge'
sys.path.insert(0, str(EXTENSION))
from dc.cfd_bridge import material_library as lib  # noqa: E402

REPO = Path(__file__).resolve().parents[5]
POOL = REPO / 'src/frontend/shared/textures'
MANIFEST = json.loads((POOL / 'materials.json').read_text())


def srgb(c):
    """THREE.ColorManagement SRGBToLinear, the editor's decode of a hex colour."""
    c = c / 255.0
    return c * 0.0773993808 if c < 0.04045 else (c * 0.9478672986 + 0.0521327014) ** 2.4


def test_reads_the_shared_pool_manifest_not_a_copy():
    assert lib.POOL_ROOT == POOL
    assert lib.MANIFEST_PATH == POOL / 'materials.json'
    assert lib.manifest() == MANIFEST
    assert lib.manifest()['schema'] == 'dcdtuni.material-library/v1'


def test_loads_by_file_path_like_render_scene_does():
    """render_scene.py is loaded standalone by the server; its reader must be too."""
    spec = importlib.util.spec_from_file_location('_standalone_material_library',
                                                  EXTENSION / 'dc/cfd_bridge/material_library.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ref = module.pool_ref('painted-steel/color.jpg')
    assert module.resolve_pool_ref(ref).path == str(POOL / 'painted-steel/color.jpg')


def test_role_entries_resolve_to_their_library_material():
    duct = lib.material('air-duct')
    galv = MANIFEST['materials']['galvanized']
    assert duct['role'] == 'galvanized' and duct['kind'] == 'air-duct'
    assert duct['roughness'] == galv['roughness'] and duct['set'] == galv['set']
    assert lib.material('cold-plate')['hueInMap'] is True
    assert lib.material('galvanized')['role'] == 'galvanized'
    assert lib.material('no-such-kind') is None


@pytest.mark.parametrize('kind,incoming,keep,expected', [
    # (expected None = the kind's manifest baseColor)
    # preset colour source: a renderer default never wins
    ('rack-side', '#7f848e', False, None),
    # model colour source: a factory default is ignored, an authored colour tints
    ('box', '#5c6370', False, None),
    ('box', '#123456', False, '#123456'),
    ('server-front', 0x4488ff, False, None),
    ('server-front', '#aa5500', False, '#aa5500'),
    # a patch overlay keeps its own colour on any tintable kind
    ('wall', '#ff0000', True, '#ff0000'),
    # not tintable: the map carries the hue whatever the caller says
    ('floor-vent', '#ff0000', True, '#ffffff'),
    # nothing handed over
    ('acu-supply', None, True, None),
])
def test_colour_rules_match_material_library_js(kind, incoming, keep, expected):
    # None: the kind's own manifest baseColor
    expected = expected or MANIFEST['materials'][kind]['baseColor']
    assert lib.color_for(kind, incoming, keep) == expected


def test_roughness_scalar_divides_by_the_map_mean_and_clamps():
    entry = MANIFEST['materials']['rack-side']
    mean = MANIFEST['sets'][entry['set']]['stats']['meanRoughness']
    assert lib.roughness_scalar('rack-side', True) == min(1.0, entry['roughness'] / mean)
    assert lib.roughness_scalar('rack-side', False) == entry['roughness']
    assert lib.roughness_scalar('custom', True) == MANIFEST['materials']['custom']['roughness']


def test_srgb_decode_is_threes_not_a_plain_divide():
    """The old Kit path wrote hex/255 straight into linear inputs (5-9x too bright on dark paint)."""
    r, g, b = lib.srgb_to_linear('#26292d')
    assert (r, g, b) == pytest.approx((srgb(0x26), srgb(0x29), srgb(0x2d)), abs=1e-15)
    assert r < 0.03 and 0x26 / 255 > 0.14


def test_pool_ref_names_the_file_and_its_manifest_sha():
    entry = MANIFEST['sets']['painted-steel']['maps']['color']
    assert lib.pool_ref('painted-steel/color.jpg') == f"pool:painted-steel/color.jpg#{entry['sha256']}"
    with pytest.raises(ValueError, match='not in the texture pool manifest'):
        lib.pool_ref('painted-steel/missing.jpg')


@pytest.mark.parametrize('url,rel', [
    ('/textures/painted-steel/color.jpg', 'painted-steel/color.jpg'),
    ('textures/painted-steel/color.jpg', 'painted-steel/color.jpg'),
    ('http://host:8441/textures/electric-box/panel_lamps.jpg?v=2', 'electric-box/panel_lamps.jpg'),
    ('/api/textures/grilles/louvre_grille.jpg', 'grilles/louvre_grille.jpg'),
])
def test_served_urls_map_to_pool_refs_like_the_browser(url, rel):
    assert lib.pool_ref_for_url(url) == lib.pool_ref(rel)


@pytest.mark.parametrize('url', ['/textures/../materials.json', '/textures/nope/x.jpg',
                                 'data:image/png;base64,AA==', '/elsewhere/painted-steel/color.jpg', None])
def test_other_urls_are_not_pool_refs(url):
    assert lib.pool_ref_for_url(url) is None


def test_resolving_a_ref_binds_the_pool_file_itself():
    ref = lib.pool_ref('containment-panel/color.png')
    texture = lib.resolve_pool_ref(ref)
    path = POOL / 'containment-panel/color.png'
    assert texture.path == str(path) and texture.rel == 'containment-panel/color.png'
    assert texture.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert texture.data == path.read_bytes()
    assert texture.suffix == '.png'


@pytest.mark.parametrize('ref', ['painted-steel/color.jpg', 'pool:painted-steel/color.jpg',
                                 'pool:../x.jpg#' + '0' * 64, 'pool:Painted/color.jpg#' + '0' * 64])
def test_malformed_refs_are_refused(ref):
    with pytest.raises(ValueError, match='pool reference'):
        lib.resolve_pool_ref(ref)


def test_a_sender_with_another_manifest_is_refused():
    ref = 'pool:painted-steel/color.jpg#' + 'f' * 64
    with pytest.raises(ValueError, match='differs between the sender and this checkout'):
        lib.resolve_pool_ref(ref)


def _fake_pool(tmp_path, monkeypatch, data, rel='painted-steel/color.jpg', declared=None):
    """A pool inside the checkout (owned_paths) whose manifest names `data`."""
    pool = tmp_path / 'pool'
    (pool / rel).parent.mkdir(parents=True)
    (pool / rel).write_bytes(data)
    sha = declared or hashlib.sha256(data).hexdigest()
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['sets']['painted-steel']['maps']['color']['sha256'] = sha
    (pool / 'materials.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(lib, 'POOL_ROOT', pool)
    monkeypatch.setattr(lib, 'MANIFEST_PATH', pool / 'materials.json')
    return f'pool:{rel}#{sha}'


def test_a_git_lfs_pointer_is_named_as_such(tmp_path, monkeypatch):
    pointer = b'version https://git-lfs.github.com/spec/v1\noid sha256:' + b'a' * 64 + b'\nsize 12\n'
    ref = _fake_pool(tmp_path, monkeypatch, pointer, declared='a' * 64)
    with pytest.raises(ValueError, match='Git LFS pointer'):
        lib.resolve_pool_ref(ref)


def test_bytes_that_do_not_match_the_manifest_are_refused(tmp_path, monkeypatch):
    ref = _fake_pool(tmp_path, monkeypatch, b'\xff\xd8\xff edited', declared='b' * 64)
    with pytest.raises(ValueError, match='sha256'):
        lib.resolve_pool_ref(ref)


def test_a_symlinked_pool_file_is_refused(tmp_path, monkeypatch):
    real = b'\xff\xd8\xff real jpeg bytes'
    ref = _fake_pool(tmp_path, monkeypatch, real)
    target = tmp_path / 'elsewhere.jpg'
    target.write_bytes(real)
    link = tmp_path / 'pool/painted-steel/color.jpg'
    link.unlink()
    link.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        lib.resolve_pool_ref(ref)


def test_kit_lighting_is_the_manifest_section():
    assert lib.kit_lighting() == MANIFEST['lighting']['kit']
