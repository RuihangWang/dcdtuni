"""Kit's reader of the shared material and texture pool manifest.

`src/frontend/shared/textures/materials.json` (dcdtuni.material-library/v1) is
the single source of every surface: which texture set a kind uses, its colour,
roughness, metalness, normal strength, tiling and UV mode, the patch presets
and the lighting. `js/material_library.js` reads it for both editors; this
module reads the same file for both Kit paths:

- the React display bridge (render_scene.py) sends a pool texture as a
  reference, `pool:<file>#<sha256>`, and `resolve_pool_ref` turns it into the
  pool's own file after the manifest, the checkout boundary (owned_paths) and
  the bytes agree on it;
- the Kit-native .cfdproj path (usdgen.py) asks `render_material(kind, ...)`
  for the same material the editor snapshot would carry, and both paths author
  it with render_scene's one UsdPreviewSurface function.

The colour, roughness and UV rules are the manifest's `colorRules`, mirrored
from js/material_library.js and geometry_scene.js `createMaterialForType` /
`spawnRenderable`; tests/test_material_library.py and
tests/test_material_parity.py hold the two sides together.

Pure: no pxr, no Kit. It is importable as `dc.cfd_bridge.material_library` and
also by file path (the server loads render_scene.py that way), so it reaches
its sibling owned_paths.py either way.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote

# Same anchor as render_scene.SCHEMA_PATH: parents[7] is src/frontend.
POOL_ROOT = Path(__file__).resolve().parents[7] / 'shared/textures'
MANIFEST_PATH = POOL_ROOT / 'materials.json'
SCHEMA = 'dcdtuni.material-library/v1'
FACE_IMAGE = 'face-image'
POOL_PREFIX = 'pool:'
# The relPath shape of material-library.schema.json, plus the sha256.
POOL_REF_PATTERN = r'^pool:[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_.-]*\.(jpg|png)#[0-9a-f]{64}$'
_POOL_REF = re.compile(POOL_REF_PATTERN)
LFS_POINTER = b'version https://git-lfs.github.com/spec/'

_state = {'key': None, 'manifest': None, 'resolved': {}, 'files': None}
_verified = {}


def _sibling(name):
    """A sibling module, as a package member or (standalone load) by file path."""
    if __package__:
        return importlib.import_module(f'{__package__}.{name}')
    key = f'_dcdtuni_material_library_{name}'
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest() -> dict:
    """The manifest, re-read when the file changes (cheap stat per call)."""
    stat = MANIFEST_PATH.stat()
    key = (str(MANIFEST_PATH), stat.st_mtime_ns, stat.st_size)
    if _state['key'] != key:
        data = json.loads(MANIFEST_PATH.read_text())
        if data.get('schema') != SCHEMA:
            raise ValueError(f'{MANIFEST_PATH}: expected a {SCHEMA} manifest, got {data.get("schema")!r}')
        _state.update(key=key, manifest=data, resolved={}, files=None)
    return _state['manifest']


def material(kind: str) -> dict | None:
    """The material of an objectKind or library role (a role entry answers with
    its library material plus its own kind/role/hueInMap), or None. Read-only."""
    data = manifest()
    resolved = _state['resolved']
    if kind in resolved:
        return resolved[kind]
    entry = data['materials'].get(kind)
    out = None
    if entry and entry.get('role'):
        target = data['materials'].get(entry['role'])
        if not target or target.get('role'):
            raise ValueError(f'material_library: {kind} names an unknown role {entry["role"]}')
        out = {**target, 'kind': kind, 'role': entry['role'], 'hueInMap': bool(entry.get('hueInMap')),
               'label': entry['label']}
    elif entry:
        out = {**entry, 'kind': kind, 'role': kind if entry.get('library') else None, 'hueInMap': False}
    resolved[kind] = out
    return out


def texture_set(name: str) -> dict | None:
    return manifest()['sets'].get(name)


def lighting() -> dict:
    return manifest()['lighting']


def kit_lighting() -> dict:
    """The Kit light rig and exposure both Kit paths apply."""
    return lighting()['kit']


def file_index() -> dict:
    """{pool-relative file: its manifest image entry (file, format, size, bytes, sha256)}."""
    data = manifest()
    if _state['files'] is None:
        index = {}
        for entry in data['sets'].values():
            for ref in entry['maps'].values():
                index[ref['file']] = ref
        for photo in data['photos'].values():
            index[photo['image']['file']] = photo['image']
        for preset in data['patchPresets']:
            index[preset['image']['file']] = preset['image']
        _state['files'] = index
    return _state['files']


def hex_of(value) -> str | None:
    """'#rrggbb' for a 0xRRGGBB number or a '#rgb' / '#rrggbb' string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return '#%06x' % (value & 0xffffff)
    if isinstance(value, float) and math.isfinite(value):
        return '#%06x' % (int(value) & 0xffffff)
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if re.fullmatch(r'#[0-9a-f]{6}', s):
        return s
    if re.fullmatch(r'#[0-9a-f]{3}', s):
        return '#' + ''.join(c * 2 for c in s[1:])
    return None


def color_for(kind: str, incoming=None, keep_user_color: bool = False) -> str:
    """The colour a mesh of `kind` shows (js material_library.colorFor).

    Not tintable: the base colour (the map carries the hue). Tintable: the base
    colour unless the mesh carries an AUTHORED colour, i.e. a patch overlay
    (`keep_user_color`) or, for `colorSource: model`, a colour that is not one
    of the renderer defaults in `factoryColors`.
    """
    entry = material(kind)
    given = hex_of(incoming)
    if entry is None:
        return given or '#9aa0a6'
    if not entry['tintable']:
        return entry['baseColor']
    if given and keep_user_color:
        return given
    if given and entry['colorSource'] == 'model' and given not in (entry.get('factoryColors') or []):
        return given
    return entry['baseColor']


def mean_roughness(set_name) -> float:
    s = texture_set(set_name) if set_name else None
    return s['stats']['meanRoughness'] if s else 1.0


def roughness_scalar(kind: str, map_bound: bool) -> float:
    """wanted / the map's mean when the roughness map is bound (clamped), else wanted."""
    entry = material(kind)
    if entry is None:
        raise ValueError(f'material_library: unknown kind {kind}')
    if not map_bound or not entry['set']:
        return entry['roughness']
    return max(0.0, min(1.0, entry['roughness'] / (mean_roughness(entry['set']) or 1.0)))


def _srgb_channel(c: float) -> float:
    # THREE.ColorManagement SRGBToLinear, so a hex colour lands on the same
    # linear value the editor's material.color holds.
    return c * 0.0773993808 if c < 0.04045 else math.pow(c * 0.9478672986 + 0.0521327014, 2.4)


def srgb_to_linear(value) -> list:
    """A hex colour (sRGB, as every manifest and project colour is) -> linear [r, g, b]."""
    hexval = hex_of(value)
    if hexval is None:
        raise ValueError(f'not a colour: {value!r}')
    n = int(hexval[1:], 16)
    return [_srgb_channel(((n >> shift) & 255) / 255.0) for shift in (16, 8, 0)]


# ── pool references ─────────────────────────────────────────────────────────

def pool_ref(rel: str) -> str:
    """`pool:<file>#<sha256>` for a file the manifest lists."""
    entry = file_index().get(rel)
    if entry is None:
        raise ValueError(f'{rel} is not in the texture pool manifest')
    return f'{POOL_PREFIX}{rel}#{entry["sha256"]}'


def is_pool_ref(value) -> bool:
    return isinstance(value, str) and value.startswith(POOL_PREFIX)


def pool_ref_for_url(url) -> str | None:
    """The pool reference a served URL (`/textures/<file>`, `textures/<file>`,
    `/api/textures/<file>`, with or without host and query) names, else None.
    Mirrors js material_library.poolFile / poolRef."""
    if not isinstance(url, str):
        return None
    path = re.split(r'[?#]', url, maxsplit=1)[0]
    try:
        path = unquote(path, errors='strict')
    except UnicodeDecodeError:
        return None
    match = re.match(r'^(?:https?://[^/]+)?/?(?:api/)?textures/(.+)$', path)
    if not match or any(seg in ('', '.', '..') for seg in match.group(1).split('/')):
        return None
    rel = match.group(1)
    return pool_ref(rel) if rel in file_index() else None


class PoolTexture:
    """A verified pool file. (A plain class: this module is also loaded by file
    path without a sys.modules entry, which dataclasses cannot resolve.)"""
    __slots__ = ('rel', 'path', 'sha256', 'data')

    def __init__(self, rel, path, sha256, data):
        self.rel, self.path, self.sha256, self.data = rel, path, sha256, data

    @property
    def suffix(self) -> str:
        return Path(self.rel).suffix.lower()


def resolve_pool_ref(ref) -> PoolTexture:
    """The pool file a reference names, verified.

    The reference must be well formed, its file listed in THIS checkout's
    manifest with the same sha256 (a sender with another manifest is refused,
    not silently shown another picture), the path inside the checkout with no
    symlink component (owned_paths), and the bytes on disk must hash to it (a
    Git LFS pointer is named as such). The bytes are returned for alpha checks;
    the file itself is what a material binds.
    """
    if not isinstance(ref, str) or not _POOL_REF.match(ref):
        raise ValueError(f'Malformed texture pool reference: {ref!r}')
    rel, sha = ref[len(POOL_PREFIX):].split('#', 1)
    if any(seg in ('', '.', '..') for seg in rel.split('/')):
        raise ValueError(f'Malformed texture pool reference: {ref!r}')
    entry = file_index().get(rel)
    if entry is None:
        raise ValueError(f'Texture pool reference {rel} is not in this checkout\'s materials.json')
    if entry['sha256'] != sha:
        raise ValueError(f'Texture pool file {rel} differs between the sender and this checkout '
                         f'(sender sha256 {sha}, materials.json {entry["sha256"]}); '
                         'reload the editor or update the checkout')
    owned = _sibling('owned_paths')
    path = owned.owned_path(str(POOL_ROOT / rel), 'texture pool file')
    stat = Path(path).stat()
    key = (path, stat.st_mtime_ns, stat.st_size, stat.st_ino)
    cached = _verified.get(key)
    if cached is not None and cached.sha256 == sha:
        return cached
    data = Path(path).read_bytes()
    if data.startswith(LFS_POINTER):
        raise ValueError(f'Texture pool file {rel} is a Git LFS pointer, not an image: run `git lfs pull`')
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        raise ValueError(f'Texture pool file {rel} does not match its materials.json sha256 '
                         f'({actual} on disk, {sha} listed)')
    texture = PoolTexture(rel=rel, path=path, sha256=sha, data=data)
    _verified[key] = texture
    return texture


# ── one kind's material, as the editor's render snapshot carries it ─────────

def _library_material(entry, color, keep_user_color, opacity, double_sided, sets_ready):
    """A kind drawn with a library role (pbr_materials.material): CDU cabinet,
    cold plate, air duct. The caller's colour wins unless the hue is in the map."""
    given = hex_of(color) if (not entry['hueInMap'] or keep_user_color) else None
    unbound = given is None and entry.get('unboundColor') and not sets_ready
    tint = given or (entry['unboundColor'] if unbound else entry['baseColor'])
    out = {
        'color': srgb_to_linear(tint), 'emissive': [0.0, 0.0, 0.0], 'opacity': float(opacity),
        'roughness': entry['roughness'], 'metalness': entry['metallic'],
        'doubleSided': bool(double_sided or entry['doubleSide']), 'unlit': False, 'alphaTest': 0,
    }
    s = texture_set(entry['set'])
    if s and sets_ready:
        out.update(map=pool_ref(s['maps']['color']['file']), mapColorSpace='srgb',
                   normalMap=pool_ref(s['maps']['normal']['file']), normalColorSpace='linear',
                   roughnessMap=pool_ref(s['maps']['roughness']['file']),
                   normalScale=[entry['normalScale'], entry['normalScale']],
                   roughness=max(0.0, min(1.0, entry['roughness'] / (s['stats']['meanRoughness'] or 1.0))),
                   wrapS='repeat', wrapT='repeat')
    return out


def render_material(kind: str, color=None, keep_user_color: bool = False, opacity: float = 1.0,
                    double_sided: bool = False, picture: str | None = None, sets_ready: bool = True) -> dict:
    """The render-scene material (sceneSnapshot RenderMaterial) of one surface.

    Exactly what the editor's snapshot sends for a mesh made by
    createMaterialForType(kind, {color, keepUserColor, opacity, doubleSide}),
    except that texture fields hold their value (a pool reference, or for a
    picture its pool reference / data URI) instead of a packet texture key.
    `kind` 'face-image' needs `picture`. An unknown kind is drawn as 'default'.
    """
    entry = material(FACE_IMAGE if kind == FACE_IMAGE else kind)
    if entry is None:
        kind, entry = 'default', material('default')
    if entry.get('role') and entry['kind'] != entry['role']:
        return _library_material(entry, color, keep_user_color, opacity, double_sided, sets_ready)
    out = {
        'color': srgb_to_linear(color_for(kind, color, keep_user_color)),
        'emissive': [0.0, 0.0, 0.0],
        'opacity': float(opacity) * entry['opacity'],
        'roughness': entry['roughness'],
        'metalness': entry['metallic'],
        'doubleSided': bool(double_sided or entry['doubleSide']),
        'unlit': False,
        'alphaTest': 0,
    }
    if kind == FACE_IMAGE:
        if picture:
            out.update(map=picture, mapColorSpace='srgb', wrapS='clamp', wrapT='clamp')
        return out
    s = texture_set(entry['set']) if entry['set'] else None
    if s and sets_ready:
        maps = s['maps']
        out.update(map=pool_ref(maps['color']['file']), mapColorSpace='srgb',
                   roughnessMap=pool_ref(maps['roughness']['file']),
                   roughness=roughness_scalar(kind, True))
        if entry['normalScale'] > 0:
            out.update(normalMap=pool_ref(maps['normal']['file']), normalColorSpace='linear',
                       normalScale=[entry['normalScale'], entry['normalScale']])
        wrap = 'clamp' if entry['wrap'] == 'clamp' else 'repeat'
        out.update(wrapS=wrap, wrapT=wrap)
    return out


def _js_round(x: float) -> float:
    return math.floor(x + 0.5)


def texture_uv(kind: str, uv_m, u_count: int = 1, mode: str | None = None, tile_m: float | None = None) -> list:
    """Texture coordinates for one planar face from its metre coordinates.

    `uv_m` is [(u, v), ...] in metres along the face's right/up axes, the UVs a
    ShapeGeometry in the face frame carries. The manifest's `uv` mode (or
    `mode`) decides the rest, as spawnRenderable and _textureRepeatFor do:
    stretch fits one map to the face, tile-per-u one map per rack unit
    (`u_count`), physical one tile per `tileM` metres (`tile_m` overrides, as a
    'tile-physical' uvNormalize does). Library roles tile at exactly 1/tileM;
    preset kinds at the editor's 3-decimal repeat.
    """
    entry = material(FACE_IMAGE if kind == FACE_IMAGE else kind) or material('default')
    mode = mode or ('stretch' if kind == FACE_IMAGE else entry['uv'])
    pts = [(float(u), float(v)) for u, v in uv_m]
    if mode in ('stretch', 'tile-per-u'):
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        ru, rv = (max(us) - min(us)) or 1.0, (max(vs) - min(vs)) or 1.0
        scale_v = max(1, int(u_count or 1)) if mode == 'tile-per-u' else 1
        return [((u - min(us)) / ru, (v - min(vs)) / rv * scale_v) for u, v in pts]
    if entry.get('role') and entry['kind'] != entry['role']:
        tile = tile_m or entry['tileM'] or 0.4
        return [(u / tile, v / tile) for u, v in pts]
    if entry['wrap'] == 'clamp' or not entry['set']:
        repeat = 1.0
    else:
        tile = tile_m or entry['tileM'] or 1.0
        repeat = _js_round(1000.0 / tile) / 1000.0
    return [(u * repeat, v * repeat) for u, v in pts]
