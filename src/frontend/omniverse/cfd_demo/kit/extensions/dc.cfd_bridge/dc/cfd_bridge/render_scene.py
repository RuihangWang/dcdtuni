"""Validated editor-render snapshot -> OpenUSD. CPU-only; Kit integration is separate.

This is a derived visual representation, never a cfdproj/cfdrun/liquidrun writer.
The shared JSON Schema is the wire contract. The small validator supports exactly
the keywords that schema uses, without requiring jsonschema inside NVIDIA Kit.

Textures (render-scene v2). A texture that is a file of the shared texture pool
(src/frontend/shared/textures, listed in materials.json) arrives as
`pool:<file>#<sha256>` and is bound IN PLACE: material_library checks the
manifest, the checkout boundary and the bytes, and the material names the pool
file itself. Only pictures with no pool file (canvases, a project's embedded
data URL) arrive as data URIs and are written to the owned texture cache.

`author_pool_material` gives the Kit-native .cfdproj path (usdgen.py) the same
UsdPreviewSurface function, so both paths author one kind identically.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time

SCHEMA_PATH = Path(__file__).resolve().parents[7] / 'shared/schema/render-scene.schema.json'
ROOT = '/World/RenderScene'
CAMERA = '/World/RenderCamera'
RESULT_ROOT = '/World/RenderResultLayers'
# Looks/m_<hash>: a material shared by primitives; Looks/p_<hash>: one primitive's own.
SHARED_LOOK_PREFIX = 'm_'
BACKGROUND_GRID_ROOT = ROOT + '/Decorations/BackgroundGrid'


def _set_constant_widths(geo, width: float):
    """One authored width for a whole curve/point cloud must be declared
    constant. USD's default widths interpolation is 'vertex', so a 1-element
    array makes hydra warn "widths primvar is not valid" per prim and fall
    back to constant anyway; declaring it keeps the intent and the log clean.
    (Streamlines in scene.py already author per-vertex widths and need none
    of this.)"""
    # Lazy import, like every other USD touch in this module: the schema side
    # of render_scene stays importable without pxr. CreateWidthsPrimvar does
    # not exist on every schema in this USD build, so author the attribute and
    # wrap it — the same Primvar(attr) path readers use.
    from pxr import UsdGeom
    attr = geo.CreateWidthsAttr([width])
    UsdGeom.Primvar(attr).SetInterpolation('constant')
    return attr


def _schema():
    return json.loads(SCHEMA_PATH.read_text())


_LIBRARY = None


def _library():
    """material_library, as a package sibling or, when this file was loaded by
    path (src/server/render_scene_files.py, backend tests), by its path."""
    global _LIBRARY
    if _LIBRARY is None:
        if __package__:
            from . import material_library as module
        else:
            spec = importlib.util.spec_from_file_location(
                '_dcdtuni_render_scene_material_library', Path(__file__).with_name('material_library.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        _LIBRARY = module
    return _LIBRARY


# Photographic textures travel as JPEG (their source format); re-encoded as
# PNG they were ~4x larger and dominated the scene transfer. Canvas/alpha
# textures stay PNG. The declared type must match the bytes.
_TEXTURE_FORMATS = {'image/png': (b'\x89PNG\r\n\x1a\n', '.png'),
                    'image/jpeg': (b'\xff\xd8\xff', '.jpg')}


def texture_bytes(uri):
    """Decode one embedded texture data URI into (bytes, file suffix)."""
    header, _, payload = uri.partition(',')
    kind = header[len('data:'):-len(';base64')] if header.startswith('data:') and header.endswith(';base64') else ''
    if kind not in _TEXTURE_FORMATS:
        raise ValueError('Texture is not PNG or JPEG')
    magic, suffix = _TEXTURE_FORMATS[kind]
    data = base64.b64decode(payload, validate=True)
    if not data.startswith(magic):
        raise ValueError(f'Texture bytes do not match {kind}')
    return data, suffix


_SCALAR_KEYS = {'type', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'}


def _plain_scalars(items):
    return items.get('type') in ('number', 'integer') and set(items) <= _SCALAR_KEYS


def _validate_scalars(values, items, path):
    """Same rules as _validate for an array of plain numbers, at C speed.

    Vertex arrays hold hundreds of thousands of numbers; one recursive call
    and one formatted path per element made validation a large share of an
    OV scene load (Base604: 10k primitives)."""
    allowed = {int} if items['type'] == 'integer' else {int, float}
    ok = set(map(type, values)) <= allowed and all(map(math.isfinite, values))
    if ok and values:
        low, high = min(values), max(values)
        ok = not (('minimum' in items and low < items['minimum']) or
                  ('maximum' in items and high > items['maximum']) or
                  ('exclusiveMinimum' in items and low <= items['exclusiveMinimum']) or
                  ('exclusiveMaximum' in items and high >= items['exclusiveMaximum']))
    if not ok:  # rare: locate the element for the same message as the slow path
        for i, child in enumerate(values):
            _validate(child, items, f'{path}[{i}]')


def _validate(value, schema, path='$'):
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    checks = {
        'object': lambda v: isinstance(v, dict),
        'array': lambda v: isinstance(v, list),
        'string': lambda v: isinstance(v, str),
        'boolean': lambda v: isinstance(v, bool),
        'number': lambda v: type(v) in (float, int) and math.isfinite(v),
        'integer': lambda v: type(v) is int,
        'null': lambda v: v is None,
    }
    if types and not any(checks[t](value) for t in types):
        raise ValueError(f'{path}: expected {types}')
    if 'const' in schema and value != schema['const']:
        raise ValueError(f'{path}: wrong constant')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: unsupported value')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError(f'{path}: missing {key}')
        for key, child in value.items():
            if key in props:
                _validate(child, props[key], f'{path}.{key}')
            elif schema.get('additionalProperties') is False:
                raise ValueError(f'{path}: unknown {key}')
            elif isinstance(schema.get('additionalProperties'), dict):
                _validate(child, schema['additionalProperties'], f'{path}.{key}')
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            raise ValueError(f'{path}: wrong array length')
        items = schema.get('items', {})
        if isinstance(items, list):
            for i, child in enumerate(value):
                if i >= len(items):
                    if schema.get('additionalItems') is False:
                        raise ValueError(f'{path}: unexpected array item')
                else:
                    _validate(child, items[i], f'{path}[{i}]')
        elif _plain_scalars(items):
            _validate_scalars(value, items, path)
        else:
            for i, child in enumerate(value):
                _validate(child, items, f'{path}[{i}]')
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and not re.search(schema['pattern'], value)):
            raise ValueError(f'{path}: invalid string')
    elif type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError(f'{path}: non-finite number')
        for name, invalid in [('minimum', lambda x: value < x), ('maximum', lambda x: value > x),
                              ('exclusiveMinimum', lambda x: value <= x), ('exclusiveMaximum', lambda x: value >= x)]:
            if name in schema and invalid(schema[name]):
                raise ValueError(f'{path}: outside {name}')
    # The overlay schema uses one cross-field condition: a null stat identity
    # is valid only when there are no preview primitives to associate with it.
    if 'if' in schema:
        try:
            _validate(value, schema['if'], path)
        except ValueError:
            branch = schema.get('else')
        else:
            branch = schema.get('then')
        if branch is not None:
            _validate(value, branch, path)



def validate_view(view):
    _validate(view, _schema()['properties']['view'])
    grid = view.get('backgroundGrid')
    if grid and (grid['divisions'] % 2 or grid['extentM'] != grid['divisions'] * grid['spacingM']):
        raise ValueError('Background grid extent/divisions/spacing mismatch')
    raster = view.get('backgroundGridRaster')
    if raster:
        line, output, binding = raster['line'], raster['output'], raster['binding']
        if any(len(x) > 128 for x in (line['owner'],output['owner'],binding['projectUid'])):
            raise ValueError('Background grid observation identity too long')
        low, high = line['aliasedRange']
        if low > high or not low <= line['effectiveWidthPx'] <= high or line['effectiveWidthPx'] > 8:
            raise ValueError('Background grid raster line range mismatch')
    camera = view.get('camera')
    if camera:
        if camera['near'] >= camera['far']:
            raise ValueError('Camera near must be below far')
        direction = [camera['target'][i] - camera['position'][i] for i in range(3)]
        up = camera['up']
        cross = [direction[1]*up[2]-direction[2]*up[1], direction[2]*up[0]-direction[0]*up[2], direction[0]*up[1]-direction[1]*up[0]]
        if sum(x*x for x in cross) < 1e-20:
            raise ValueError('Degenerate camera direction/up')


def validate_snapshot(packet):
    _validate(packet, _schema())
    validate_view(packet['view'])
    seen = set()
    for p in packet['primitives']:
        if p['id'] in seen:
            raise ValueError('Duplicate primitive identity')
        seen.add(p['id'])
        count, remainder = divmod(len(p['positions']), 3)
        if remainder or any(i >= count for i in p['indices']):
            raise ValueError('Invalid positions or vertex index')
        for name, components in [('normals', 3), ('uvs', 2), ('colors', 3)]:
            if name in p and len(p[name]) != count * components:
                raise ValueError(f'Invalid {name} length')
        stride = {'triangles': 3, 'lines': 2, 'points': 1}[p['kind']]
        if len(p['indices']) % stride:
            raise ValueError('Incomplete primitive topology')
        for key in ('map', 'normalMap', 'roughnessMap', 'metalnessMap'):
            texture = p['material'].get(key)
            if texture and (texture not in packet['textures'] or 'uvs' not in p):
                raise ValueError('Unresolved texture or missing UV')
    library = _library()
    for uri in packet['textures'].values():
        if library.is_pool_ref(uri):
            library.resolve_pool_ref(uri)
        else:
            texture_bytes(uri)


class SceneTransfer:
    """One bounded, ordered transfer; no unbounded assembly on a shared Kit process."""
    def __init__(self, validator=validate_snapshot):
        self.current = None
        if not callable(validator):
            raise TypeError('Scene transfer validator must be callable')
        self.validator = validator

    LIMIT = 128 * 1024 * 1024

    def begin(self, request_id, chunks, encoding=None):
        """``encoding`` 'deflate-base64': the chunks join to base64 of a zlib
        stream (the browser's CompressionStream('deflate')); finish inflates it
        under the same 128 MiB bound as plain text."""
        if not isinstance(request_id, str) or not request_id or type(chunks) is not int or not 1 <= chunks <= 16384:
            raise ValueError('Invalid scene transfer header')
        if encoding not in (None, 'deflate-base64'):
            raise ValueError('Unsupported scene transfer encoding')
        self.current = {'id': request_id, 'count': chunks, 'data': [], 'size': 0, 'at': time.monotonic(),
                        'encoding': encoding}

    def chunk(self, request_id, index, data):
        c = self.current
        if not c or c['id'] != request_id or time.monotonic() - c['at'] > 120:
            raise ValueError('Missing or expired scene transfer')
        if type(index) is not int or index != len(c['data']) or index >= c['count'] or not isinstance(data, str) or len(data) > 16000:
            self.current = None
            raise ValueError('Out-of-order or oversized scene chunk')
        c['size'] += len(data.encode('utf-8'))
        if c['size'] > self.LIMIT:
            self.current = None
            raise ValueError('Scene transfer exceeds 128 MiB')
        c['data'].append(data)
        c['at'] = time.monotonic()

    def progress(self, scene_request_id):
        c = self.current
        if (not c or not isinstance(scene_request_id, str) or not scene_request_id
                or c['id'] != scene_request_id or time.monotonic() - c['at'] > 120):
            raise ValueError('Missing or expired scene transfer')
        return len(c['data'])

    def finish(self, request_id):
        c = self.current
        self.current = None
        if not c or c['id'] != request_id or len(c['data']) != c['count']:
            raise ValueError('Incomplete scene transfer')
        text = ''.join(c['data'])
        if c.get('encoding') == 'deflate-base64':
            import binascii
            import zlib
            try:
                inflater = zlib.decompressobj()
                raw = inflater.decompress(base64.b64decode(text, validate=True), self.LIMIT + 1)
            except (binascii.Error, zlib.error) as exc:
                raise ValueError(f'Corrupt deflated scene transfer: {exc}') from None
            if len(raw) > self.LIMIT or inflater.unconsumed_tail:
                raise ValueError('Scene transfer exceeds 128 MiB')
            if not inflater.eof:
                raise ValueError('Truncated deflated scene transfer')
            text = raw.decode('utf-8')
        packet = json.loads(text)
        self.validator(packet)
        return packet


def _tuples(values, n):
    return [tuple(values[i:i+n]) for i in range(0, len(values), n)]


def _vec_array(kind, values, n, indices=None):
    """A Vt array of n-vectors from a flat JSON list, converted in one go
    (per-element tuples were most of Kit's authoring time); `indices` picks
    elements, as a curve or point cloud lists its points by index."""
    import numpy as np
    from pxr import Vt
    array = np.asarray(values, dtype=np.float32).reshape(-1, n)
    if indices is not None:
        array = array[np.asarray(indices, dtype=np.int64)]
    return getattr(Vt, kind).FromNumpy(np.ascontiguousarray(array))


def _attribute(prim, name, type_name, value, uniform=False, interpolation=None, custom=False):
    from pxr import Sdf
    attr = Sdf.AttributeSpec(prim, name, type_name,
                             Sdf.VariabilityUniform if uniform else Sdf.VariabilityVarying, custom)
    attr.default = value
    if interpolation:
        attr.SetInfo('interpolation', interpolation)
    return attr


def _geometry_spec(layer, path, p, material_path):
    """One primitive as the Sdf specs UsdGeom.Mesh/BasisCurves/Points.Define,
    their Create*Attr, PrimvarsAPI, MakeInvisible, AddTransformOp and
    MaterialBindingAPI.Bind author (the parent spec must exist)."""
    from pxr import Gf, Sdf, Vt
    T = Sdf.ValueTypeNames
    prim = Sdf.PrimSpec(layer.GetPrimAtPath(path.rsplit('/', 1)[0]), path.rsplit('/', 1)[1], Sdf.SpecifierDef,
                        {'triangles': 'Mesh', 'lines': 'BasisCurves', 'points': 'Points'}[p['kind']])
    if p['kind'] == 'triangles':
        _attribute(prim, 'points', T.Point3fArray, _vec_array('Vec3fArray', p['positions'], 3))
        _attribute(prim, 'faceVertexIndices', T.IntArray, _int_array(p['indices']))
        _attribute(prim, 'faceVertexCounts', T.IntArray, Vt.IntArray(len(p['indices']) // 3, 3))
        _attribute(prim, 'subdivisionScheme', T.Token, 'none', uniform=True)
        if 'normals' in p:
            _attribute(prim, 'normals', T.Normal3fArray, _vec_array('Vec3fArray', p['normals'], 3), interpolation='vertex')
        if 'uvs' in p:
            _attribute(prim, 'primvars:st', T.TexCoord2fArray, _vec_array('Vec2fArray', p['uvs'], 2), interpolation='vertex')
        if 'colors' in p:
            _attribute(prim, 'primvars:displayColor', T.Color3fArray, _vec_array('Vec3fArray', p['colors'], 3),
                       interpolation='vertex')
    else:
        if p['kind'] == 'lines':
            _attribute(prim, 'type', T.Token, 'linear', uniform=True)
            _attribute(prim, 'curveVertexCounts', T.IntArray, Vt.IntArray(len(p['indices']) // 2, 2))
        _attribute(prim, 'points', T.Point3fArray, _vec_array('Vec3fArray', p['positions'], 3, p['indices']))
        _attribute(prim, 'widths', T.FloatArray, Vt.FloatArray([float(p.get('width', .001))]), interpolation='constant')
        if 'colors' in p:
            _attribute(prim, 'primvars:displayColor', T.Color3fArray,
                       _vec_array('Vec3fArray', p['colors'], 3, p['indices']), interpolation='vertex')
        if 'uvs' in p:
            _attribute(prim, 'primvars:st', T.TexCoord2fArray, _vec_array('Vec2fArray', p['uvs'], 2, p['indices']),
                       interpolation='vertex')
    prim.SetInfo('displayName', p['name'])
    data = {'modelUid': p['modelUid'], 'primitiveId': p['id']}
    if p.get('displayRole') == 'room-shell':
        data['displayRole'] = 'room-shell'
    if p.get('equipmentUid'):
        data['equipmentUid'] = p['equipmentUid']
    if p.get('liquidPart'):
        data['liquidPart'] = p['liquidPart']
    prim.customData = {'dcdtuni': data}
    _attribute(prim, 'xformOp:transform', T.Matrix4d, Gf.Matrix4d(*p['matrix']))
    _attribute(prim, 'xformOpOrder', T.TokenArray, Vt.TokenArray(['xformOp:transform']), uniform=True)
    _attribute(prim, 'doubleSided', T.Bool, bool(p['material']['doubleSided']), uniform=True)
    if not p['visible']:
        _attribute(prim, 'visibility', T.Token, 'invisible')
    if casts_no_shadow(p['material'], p.get('displayRole')):
        _attribute(prim, 'primvars:doNotCastShadows', T.Bool, True, custom=True)
    binding = Sdf.RelationshipSpec(prim, 'material:binding', False)
    binding.targetPathList.explicitItems = [Sdf.Path(material_path)]
    prim.SetInfo('apiSchemas', Sdf.TokenListOp.Create(prependedItems=['MaterialBindingAPI']))


def _int_array(values):
    import numpy as np
    from pxr import Vt
    return Vt.IntArray.FromNumpy(np.asarray(values, dtype=np.int32))


def _texture_shader(stage, path, asset, color_space, uv, scale, bias, wrap_s='repeat', wrap_t='repeat'):
    from pxr import Gf, Sdf, UsdShade
    texture = UsdShade.Shader.Define(stage, path)
    texture.CreateIdAttr('UsdUVTexture')
    texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(str(asset)))
    texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB' if color_space == 'srgb' else 'raw')
    texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(), 'result')
    texture.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set(wrap_s)
    texture.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set(wrap_t)
    texture.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*scale))
    texture.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*bias))
    texture.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
    texture.CreateOutput('a', Sdf.ValueTypeNames.Float)
    texture.CreateOutput('g', Sdf.ValueTypeNames.Float)
    texture.CreateOutput('b', Sdf.ValueTypeNames.Float)
    return texture


def _texture_has_alpha(data, suffix):
    """Identify PNG alpha carriers; JPEG has no alpha channel."""
    if suffix != '.png':
        return False
    if len(data) >= 26 and data[12:16] == b'IHDR' and data[25] in (4, 6):
        return True
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], 'big')
        kind = data[offset + 4:offset + 8]
        if offset + 12 + length > len(data):
            break
        if kind == b'tRNS':
            return True
        if kind in (b'IDAT', b'IEND'):
            break
        offset += 12 + length
    return False


# How much of what lies behind a fractional-cutout sheet RTX shows, as display
# contrast (behind-white minus behind-black over the same pair seen bare),
# against the cutout opacity. Measured 2026-10-23 in kit-cae 2.0.1 with the
# bridge's own exposure and light rig: a 0.6-grey sheet 1 m above white/black
# tiles, seen from above, no shadow from the sheet; single- and double-sided
# sheets read the same. RTX shows about (1 - a)^2 of the background in linear
# terms (both sides of the sheet count), less again once tone-mapped.
RTX_CUTOUT_TRANSMISSION = ((0.0, 1.0), (0.05, 0.815), (0.1, 0.673), (0.15, 0.547), (0.2, 0.46),
                           (0.3, 0.336), (0.4, 0.242), (0.5, 0.185), (0.6, 0.118), (0.7, 0.086),
                           (0.8, 0.055), (0.85, 0.042), (1.0, 0.0))


def rtx_cutout_opacity(opacity, double_sided):
    """The cutout opacity at which RTX shows as much behind a surface as Three does.

    Three blends a transparent surface over what is behind it in display
    space: (1 - a) of it shows, and (1 - a)^2 for a double-sided surface, which
    Three draws twice (back faces, then front). Passed straight through, the
    editor's 0.6 ceiling hid the racks below it in Kit (0.12 of them shown
    against Three's 0.16, and dimmer behind a bright sheet).
    """
    if opacity >= 1 or opacity <= 0:
        return opacity
    target = (1 - opacity) ** (2 if double_sided else 1)
    table = RTX_CUTOUT_TRANSMISSION
    for (a0, t0), (a1, t1) in zip(table, table[1:]):
        if t1 <= target <= t0:
            return a0 + (a1 - a0) * (t0 - target) / (t0 - t1)
    return opacity


# The display value a translucent sheet's own light is matched at: a light
# grey, the room shell's (the ceiling and raised floor tiles, ~217 of 255).
TRANSLUCENT_REFERENCE = 0.85
# How much a double-sided sheet's underside adds in Three's back-face pass,
# relative to its lit top.
THREE_BACK_PASS = 0.5


def _srgb_to_linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def translucent_albedo_scale(opacity, double_sided):
    """What a translucent surface's colour is scaled by in Kit.

    Three blends a translucent surface over what is behind it in display
    (sRGB) space, RTX in linear light before tone mapping. sRGB is concave, so
    the same coverage of a light sheet over a dark room reads much brighter in
    RTX: Shinaide's raised floor and ceiling came out 30-40 grey levels lighter
    in Omniverse with the same share of what lies behind showing through. The
    sheet's own light is scaled so that, blended in linear light at RTX's
    coverage of the calibrated cutout (1 - (1 - a')^2, measured with the Kit
    probe), it gives Three's display-space blend of a light grey: its front pass
    a and, double-sided, (1 - a) a of a dimmer back face.
    """
    if not 0 < opacity < 1:
        return 1.0
    cutout = rtx_cutout_opacity(opacity, double_sided)
    kit_coverage = 1 - (1 - cutout) ** 2
    three_coverage = opacity + ((1 - opacity) * opacity * THREE_BACK_PASS if double_sided else 0.0)
    ref = TRANSLUCENT_REFERENCE
    return min(1.0, _srgb_to_linear(three_coverage * ref) / (kit_coverage * _srgb_to_linear(ref)))


def kit_look(spec):
    """The editor's material as Kit draws it. A translucent surface's opacity
    becomes the RTX cutout that shows what Three shows behind it
    (rtx_cutout_opacity), and its colour is scaled so its own light matches
    too (translucent_albedo_scale); an alpha-tested cutout and an opaque
    surface are drawn as given. Both Kit paths, the display bridge and the
    native .cfdproj one, author through it."""
    if 0 < spec['opacity'] < 1 and not spec.get('alphaTest'):
        double_sided = bool(spec.get('doubleSided'))
        scale = translucent_albedo_scale(spec['opacity'], double_sided)
        return dict(spec, opacity=rtx_cutout_opacity(spec['opacity'], double_sided),
                    color=[c * scale for c in spec['color']])
    return spec


# Below this a translucent surface casts no shadow, in both renderers
# (shared/js/scene_lighting.js SHADOW_MIN_OPACITY).
SHADOW_MIN_OPACITY = 0.6


def casts_no_shadow(spec, display_role=None):
    """Whether a surface stays out of the shadows, as in the editor: a see-through
    one (opacity under SHADOW_MIN_OPACITY), and the room's own walls and
    ceiling, which receive shadows but cast none -- Kit's oblique key light
    would otherwise leave the whole room in their shadow."""
    see_through = 0 < spec['opacity'] < SHADOW_MIN_OPACITY and not spec.get('alphaTest')
    return see_through or display_role == 'room-shell'


def _uses_fractional_material(source, alpha_texture_keys):
    return source['opacity'] < 1 or source.get('map') in alpha_texture_keys


def _publish_fractional_mdl(texture_dir, asset_prefix):
    """Publish the tracked adapter beside existing scene-owned texture assets."""
    data = Path(__file__).with_name('DcdtuniFractionalPreview.mdl').read_bytes()
    texture_dir.mkdir(parents=True, exist_ok=True)
    file = texture_dir / ('DcdtuniFractionalPreview_' + hashlib.sha256(data).hexdigest()[:16] + '.mdl')
    if not file.exists():
        file.write_bytes(data)
    elif file.read_bytes() != data:
        raise ValueError('Fractional material cache does not match tracked source')
    return ((Path(asset_prefix) / file.name).as_posix()
            if asset_prefix is not None else str(file.resolve()))


def _fractional_surface(stage, material, source_surface, asset):
    """Native whole-material alpha; retain the universal graph as USD fallback."""
    from pxr import Sdf, UsdShade
    if asset is None:
        raise ValueError('Fractional material adapter asset is missing')
    native = UsdShade.Shader.Define(stage, str(material.GetPath()) + '/FractionalSurface')
    native.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
    native.SetSourceAsset(Sdf.AssetPath(asset), 'mdl')
    native.SetSourceAssetSubIdentifier('DcdtuniFractionalPreview', 'mdl')
    for source in source_surface.GetInputs():
        # A Shader input is not a pass-through source in UsdShade resolution.
        # Material interface inputs carry the common value/texture connection.
        shared = material.CreateInput(source.GetBaseName(), source.GetTypeName())
        value = source.Get()
        if value is not None:
            shared.Set(value)
        connections = source.GetAttr().GetConnections()
        if connections and not shared.GetAttr().SetConnections(connections):
            raise ValueError('Could not retain fractional material texture input')
        target = native.CreateInput(source.GetBaseName(), source.GetTypeName())
        if not source.ConnectToSource(shared) or not target.ConnectToSource(shared):
            raise ValueError('Could not link shared fractional material input')
    native.CreateOutput('out', Sdf.ValueTypeNames.Token)
    for output in (material.CreateSurfaceOutput('mdl'),
                   material.CreateDisplacementOutput('mdl'), material.CreateVolumeOutput('mdl')):
        if not output.ConnectToSource(native.ConnectableAPI(), 'out'):
            raise ValueError('Could not bind native fractional material')


def _texture_assets(textures, texture_dir, asset_prefix=None):
    """{texture key: USD asset path} and the keys whose image carries alpha.

    A pool reference binds the pool file itself (absolute, or relative to the
    exported USD when `asset_prefix` is given); a data URI is written once to
    the owned `texture_dir` as <sha256>.<ext>. Every reference is verified
    before anything is written.
    """
    library = _library()
    texture_dir = Path(texture_dir)
    usd_dir = None
    if asset_prefix is not None:
        usd_dir = texture_dir
        for _ in Path(asset_prefix).parts:
            usd_dir = usd_dir.parent
    pooled = {key: library.resolve_pool_ref(value)
              for key, value in textures.items() if library.is_pool_ref(value)}
    assets, alpha_texture_keys = {}, set()
    for key, value in textures.items():
        if key in pooled:
            texture = pooled[key]
            if _texture_has_alpha(texture.data, texture.suffix):
                alpha_texture_keys.add(key)
            assets[key] = (Path(os.path.relpath(texture.path, usd_dir.resolve())).as_posix()
                           if usd_dir is not None else texture.path)
            continue
        data, suffix = texture_bytes(value)
        if _texture_has_alpha(data, suffix):
            alpha_texture_keys.add(key)
        texture_dir.mkdir(parents=True, exist_ok=True)
        file = texture_dir / (hashlib.sha256(data).hexdigest() + suffix)
        if not file.exists():
            file.write_bytes(data)
        assets[key] = ((Path(asset_prefix) / file.name).as_posix()
                       if asset_prefix is not None else str(file.resolve()))
    return assets, alpha_texture_keys


def tracked_fractional_mdl():
    """The tracked translucency adapter, for stages with no owned cache of their own."""
    return str(Path(__file__).with_name('DcdtuniFractionalPreview.mdl'))


def author_pool_material(stage, path, source, texture_dir=None, mdl_asset=None):
    """One render-scene material whose texture fields hold their VALUES (pool
    references or data URIs), as material_library.render_material returns them.

    The Kit-native .cfdproj path authors through here, so a kind gets exactly
    the UsdPreviewSurface graph the display bridge gives the same kind. Data
    URIs need an owned `texture_dir`; pool references never touch it.
    """
    keys = ('map', 'normalMap', 'roughnessMap', 'metalnessMap')
    textures = {source[k]: source[k] for k in keys if source.get(k)}
    if texture_dir is None and any(not _library().is_pool_ref(v) for v in textures.values()):
        raise ValueError('An embedded picture needs an owned texture directory')
    assets, alpha = _texture_assets(textures, texture_dir or '.', None)
    return _material(stage, path, kit_look(source), assets, False, alpha, mdl_asset or tracked_fractional_mdl())


def author_kit_lights(stage, root):
    """The manifest's Kit rig (lighting.kit) under `root`: a dome, a key and a
    fill, and the shadow light (lighting.shadow), dark until shadows come on.

    Only the shadow light casts: the key sits within twenty degrees of the
    default view, so its shadows fell behind what cast them and could not be
    seen; the shadow light comes from the upper left, as in the editor."""
    from pxr import Gf, UsdGeom, UsdLux
    library = _library()
    kit = library.kit_lighting()
    shadow = library.lighting()['shadow']
    UsdGeom.Xform.Define(stage, root)
    dome = UsdLux.DomeLight.Define(stage, root + '/Dome')
    dome.CreateIntensityAttr(float(kit['domeIntensity']))
    dome.CreateColorAttr(Gf.Vec3f(*library.srgb_to_linear(kit['domeColor'])))
    # Reflections only: the editor's room environment lights them more.
    dome.CreateSpecularAttr(float(kit['domeSpecular']))
    lights = {'Dome': dome}
    for name, intensity, angle, color, rotate in (
            ('Key', kit['keyIntensity'], kit['keyAngle'], kit['keyColor'], kit['keyRotateXYZ']),
            ('Fill', kit['fillIntensity'], kit['fillAngle'], kit['fillColor'], kit['fillRotateXYZ']),
            ('Shadow', 0.0, shadow['kitAngle'], kit['keyColor'], shadow['rotateXYZ'])):
        light = UsdLux.DistantLight.Define(stage, root + '/' + name)
        light.CreateIntensityAttr(float(intensity))
        light.CreateAngleAttr(float(angle))
        light.CreateColorAttr(Gf.Vec3f(*library.srgb_to_linear(color)))
        UsdGeom.Xformable(light).AddRotateXYZOp().Set(Gf.Vec3f(*[float(v) for v in rotate]))
        lights[name] = light
    for name, light in lights.items():
        UsdLux.ShadowAPI.Apply(light.GetPrim()).CreateShadowEnableAttr(name == 'Shadow')
    return lights


# lighting.toneMapping -> RTX /rtx/post/tonemap/op (omni.rtx.settings.core lists
# Clamp, Linear, Reinhard, Modified Reinhard, HejlHableAlu, HableUc2, Aces, Iray).
_RTX_TONEMAP_OP = {'aces-filmic': 6, 'none': 1}


def retire_foreign_lights(stage, keep_root):
    """Deactivate every light on `stage` outside `keep_root`; returns their paths.

    Kit's default new-stage template ("sunlight") leaves /Environment/defaultLight
    on every new stage: a 3000-intensity DistantLight (UsdLux 25.05 adds exposure
    1.5) with no rotation, which on this Y-up stage shines horizontally along -Z.
    It sat on top of the manifest rig, so faces turned to +Z were lit several times
    over and changing the manifest lights barely moved them. The scene's own rig is
    the only light the editors' look is calibrated against. Deactivated, not
    deleted: the prims belong to whoever authored them.
    """
    from pxr import Sdf, UsdLux
    keep = Sdf.Path(keep_root)
    retired = []
    for prim in stage.Traverse():
        if prim.HasAPI(UsdLux.LightAPI) and not prim.GetPath().HasPrefix(keep):
            prim.SetActive(False)
            retired.append(str(prim.GetPath()))
    return retired


def backdrop_linear():
    """Kit's constant backdrop (materials.json lighting.kit.background), linear RGB."""
    return tuple(_library().srgb_to_linear(_library().kit_lighting()['background']))


def kit_exposure_settings():
    """(carb setting path, value) pairs for the manifest's RTX tone mapping and exposure.

    With `autoExposure` off the frame is exposed by the manifest's fixed camera
    (`fixedExposure`: ISO, exposure time in seconds, f-number), as the editors
    expose by one fixed toneMappingExposure: an object then has the same
    brightness in every view. The histogram meters each frame instead, so a dark
    backdrop lifted the whole scene and a black rack read as mid grey.
    """
    library = _library()
    kit = library.kit_lighting()
    settings = [('/rtx/post/histogram/enabled', bool(kit['autoExposure'])),
                ('/rtx/post/tonemap/exposureCompensation', float(kit['exposureCompensation'])),
                ('/rtx/shadows/enabled', bool(kit['shadows']))]
    # A constant backdrop (the editors' viewport colour) instead of the dome seen
    # as sky behind the room.
    settings += [('/rtx/background/source/type', 2),
                 ('/rtx/background/source/color', tuple(library.srgb_to_linear(kit['background'])))]
    op = _RTX_TONEMAP_OP.get(library.lighting()['toneMapping'])
    if op is not None:
        settings.append(('/rtx/post/tonemap/op', op))
    if not kit['autoExposure']:
        camera = kit['fixedExposure']
        settings += [('/rtx/post/tonemap/filmIso', float(camera['filmIso'])),
                     ('/rtx/post/tonemap/exposureTime', float(camera['exposureTime'])),
                     ('/rtx/post/tonemap/fNumber', float(camera['fNumber']))]
    # Further RTX render settings the manifest pins for an editor scene.
    settings += [(path, value) for path, value in sorted(kit.get('rtxSettings', {}).items())]
    return tuple(settings)


# Lights off on the Three side is a flat ambient term; here the directional pair
# goes dark and the dome carries the scene a little brighter, the same intent.
LIGHTS_OFF_DOME = 1.6


def lighting_targets(lighting):
    """The rig a view's lighting asks for: dome/key/fill intensity, RTX shadows, exposure compensation.

    `lighting` is the render scene's view.lighting ({enabled, scale, preset,
    shadows}); the preset is a key of materials.json lighting.presets, the same
    table the editors scale their camera lights by.
    """
    library = _library()
    kit = library.kit_lighting()
    presets = library.lighting().get('presets') or {}
    preset = presets.get(lighting.get('preset')) or presets.get('standard') or {}
    on = bool(lighting.get('enabled', True))
    k = float(lighting.get('scale', 1.0)) if on else 0.0
    shadows = bool(lighting.get('shadows', kit['shadows']))
    # As in the editor: with shadows on, the shadow light takes over part of
    # the key, so the shadows fall from light that is really there.
    share = float(library.lighting()['shadow']['share']) if shadows else 0.0
    key = float(kit['keyIntensity']) * preset.get('kitKey', 1.0) * k
    return {
        'Dome': float(kit['domeIntensity']) * preset.get('kitDome', 1.0) * (k if on else LIGHTS_OFF_DOME),
        'Key': key * (1 - share),
        'Fill': float(kit['fillIntensity']) * preset.get('kitFill', 1.0) * k,
        'Shadow': key * share,
        'shadows': shadows,
        'exposureCompensation': float(kit['exposureCompensation']) + float(preset.get('kitExposureStops', 0.0)),
    }


def apply_lighting(stage, lighting, settings_api=None):
    """Scale the scene rig (ROOT/Lighting) and set RTX shadows/exposure for view.lighting; returns the targets."""
    if not lighting:
        return None
    from pxr import UsdLux
    targets = lighting_targets(lighting)
    for name in ('Dome', 'Key', 'Fill', 'Shadow'):
        prim = stage.GetPrimAtPath(ROOT + '/Lighting/' + name)
        if prim:
            UsdLux.LightAPI(prim).GetIntensityAttr().Set(targets[name])
    if settings_api is None:
        try:
            import carb.settings
            settings_api = carb.settings.get_settings()
        except Exception:  # noqa: BLE001 - outside Kit (tests, file export) there is no RTX to set
            settings_api = None
    if settings_api is not None:
        settings_api.set('/rtx/shadows/enabled', targets['shadows'])
        settings_api.set('/rtx/post/tonemap/exposureCompensation', targets['exposureCompensation'])
    return targets


def apply_kit_exposure(settings_api, log=print):
    """Set kit_exposure_settings() on a carb settings object; log old -> new."""
    changed = []
    for path, value in kit_exposure_settings():
        before = settings_api.get(path)
        settings_api.set(path, value)
        changed.append(f'{path.rsplit("/", 1)[-1]}={before}->{value}')
    log('[dc.cfd_bridge] render exposure (materials.json lighting.kit): ' + ', '.join(changed))


def _material(stage, path, source, assets, has_colors, alpha_texture_keys=(), mdl_asset=None):
    from pxr import Gf, Sdf, UsdShade
    mat = UsdShade.Material.Define(stage, path)
    surface = UsdShade.Shader.Define(stage, path + '/Surface')
    surface.CreateIdAttr('UsdPreviewSurface')
    color = source['color']
    surface.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*(color if not source['unlit'] else [0, 0, 0])))
    surface.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*(color if source['unlit'] else source['emissive'])))
    for name, value in [('roughness', source['roughness']), ('metallic', source['metalness']),
                        ('opacity', source['opacity']), ('opacityThreshold', source.get('alphaTest', 0))]:
        surface.CreateInput(name, Sdf.ValueTypeNames.Float).Set(value)
    if source.get('emissionOnly'):
        # No specular lobe either: only the emission is seen, whatever the lights.
        surface.CreateInput('useSpecularWorkflow', Sdf.ValueTypeNames.Int).Set(1)
        surface.CreateInput('specularColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0, 0, 0))
    target = 'emissiveColor' if source['unlit'] else 'diffuseColor'
    if has_colors:
        colors = UsdShade.Shader.Define(stage, path + '/VertexColor')
        colors.CreateIdAttr('UsdPrimvarReader_float3')
        colors.CreateInput('varname', Sdf.ValueTypeNames.String).Set('displayColor')
        colors.CreateOutput('result', Sdf.ValueTypeNames.Float3)
        surface.GetInput(target).ConnectToSource(colors.ConnectableAPI(), 'result')
    if any(source.get(key) for key in ('map', 'normalMap', 'roughnessMap', 'metalnessMap')):
        uv = UsdShade.Shader.Define(stage, path + '/UV')
        uv.CreateIdAttr('UsdPrimvarReader_float2')
        uv.CreateInput('varname', Sdf.ValueTypeNames.String).Set('st')
        uv.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        if source.get('map'):
            tex = _texture_shader(stage, path + '/ColorTexture', assets[source['map']], source.get('mapColorSpace', 'srgb'), uv,
                                  [*color, source['opacity']], [0, 0, 0, 0], source.get('wrapS', 'repeat'), source.get('wrapT', 'repeat'))
            surface.GetInput(target).ConnectToSource(tex.ConnectableAPI(), 'rgb')
            surface.GetInput('opacity').ConnectToSource(tex.ConnectableAPI(), 'a')
        if source.get('normalMap'):
            sx, sy = source.get('normalScale', [1, 1])
            tex = _texture_shader(stage, path + '/NormalTexture', assets[source['normalMap']], source.get('normalColorSpace', 'linear'), uv,
                                  [2*sx, 2*sy, 2, 1], [-sx, -sy, -1, 0], source.get('wrapS', 'repeat'), source.get('wrapT', 'repeat'))
            surface.CreateInput('normal', Sdf.ValueTypeNames.Normal3f).ConnectToSource(tex.ConnectableAPI(), 'rgb')
        for key, input_name, channel, factor in [('roughnessMap', 'roughness', 'g', source['roughness']),
                                                  ('metalnessMap', 'metallic', 'b', source['metalness'])]:
            if source.get(key):
                tex = _texture_shader(stage, path + '/' + key, assets[source[key]], 'linear', uv,
                                      [factor, factor, factor, 1], [0, 0, 0, 0], source.get('wrapS', 'repeat'), source.get('wrapT', 'repeat'))
                surface.GetInput(input_name).ConnectToSource(tex.ConnectableAPI(), channel)
    surface.CreateOutput('surface', Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), 'surface')
    if _uses_fractional_material(source, alpha_texture_keys):
        _fractional_surface(stage, mat, surface, mdl_asset)
    return mat


def index_render_prims(stage):
    """(modelUid, equipmentUid, liquidPart) -> geometry prim paths of the
    authored editor scene. Result colouring targets parts through this index
    (stable customData), never through positional primitive ids."""
    out = {}
    geometry = stage.GetPrimAtPath(ROOT + '/Geometry')
    if not geometry:
        return out
    for prim in geometry.GetChildren():
        key = tuple(prim.GetCustomDataByKey(k) or '' for k in
                    ('dcdtuni:modelUid', 'dcdtuni:equipmentUid', 'dcdtuni:liquidPart'))
        out.setdefault(key, []).append(str(prim.GetPath()))
    return out


def _surface_colour_attr(stage, material_path):
    """The Surface input carrying a material's base colour: diffuse, or
    emissive for unlit materials (authored with a black diffuse)."""
    shader = stage.GetPrimAtPath(material_path + '/Surface')
    if not shader:
        return None
    # Fractional native and universal surfaces share Material inputs. Result
    # colours must update that interface so both render contexts change/restore.
    carrier = shader.GetParent() if shader.GetParent().GetChild('FractionalSurface') else shader
    diffuse = carrier.GetAttribute('inputs:diffuseColor')
    emissive = carrier.GetAttribute('inputs:emissiveColor')
    black = diffuse and not diffuse.GetConnections() and tuple(diffuse.Get() or (0, 0, 0)) == (0, 0, 0)
    lit_emissive = emissive and (emissive.GetConnections() or any(emissive.Get() or (0, 0, 0)))
    return emissive if black and lit_emissive else diffuse


def _own_materials(stage, geometry_paths, saved):
    """Give each primitive its OWN Looks material before it is painted.

    Primitives share one material per look (author_scene), so painting the
    bound one would recolour every primitive that shares it. On a primitive's
    first touch the shared material is copied to Looks/<primitive leaf> and the
    primitive rebound to the copy; ``saved`` records the original binding so
    restore_colours rebinds it and drops the copy. Copies and rebinds are plain
    layer edits in one change block: one recomposition for the whole batch."""
    from pxr import Sdf
    layer = stage.GetRootLayer()
    owned = {}
    with Sdf.ChangeBlock():
        for path in geometry_paths:
            own = ROOT + '/Looks/' + path.rsplit('/', 1)[-1]
            if path in saved:
                owned[path] = saved[path]['material']
                continue
            prim = layer.GetPrimAtPath(path)
            rel = prim.relationships.get('material:binding') if prim else None
            targets = list(rel.targetPathList.explicitItems) if rel else []
            if len(targets) != 1 or not str(targets[0]).startswith(ROOT + '/Looks/'):
                continue
            bound = str(targets[0])
            if bound != own:
                if layer.GetPrimAtPath(own) and not _remove_spec(layer, own):
                    continue
                if not Sdf.CopySpec(layer, bound, layer, own):
                    continue
                rel.targetPathList.explicitItems = [Sdf.Path(own)]
            saved[path] = {'material': own, 'binding': bound}
            owned[path] = own
    return owned


def _remove_spec(layer, path):
    from pxr import Sdf
    spec = layer.GetPrimAtPath(path)
    parent = spec.nameParent if spec else None
    if not parent:
        return False
    del parent.nameChildren[Sdf.Path(path).name]
    return True


def override_colours(stage, colours, saved, glow=0.0):
    """Paint {geometry_path: (r, g, b) in 0..1} onto each primitive's OWN
    material (_own_materials copies it out of the shared look first, so nothing
    bleeds onto the primitives sharing it). ``saved`` keeps each primitive's
    original binding for restore_colours. ``glow`` > 0 also sets a lit
    surface's emissive colour to glow * rgb, so a result colour stays readable
    in shadow or under a translucent raised floor (unlit surfaces are already
    emissive). Returns the number of primitives painted."""
    from pxr import Gf, Sdf
    owned = _own_materials(stage, list(colours), saved)
    painted = 0
    with Sdf.ChangeBlock():
        for path, rgb in colours.items():
            attr = _surface_colour_attr(stage, owned[path]) if path in owned else None
            if not attr:
                continue
            targets = [(attr, rgb)]
            if glow > 0 and attr.GetName() == 'inputs:diffuseColor':
                emissive = attr.GetPrim().GetAttribute('inputs:emissiveColor')
                if emissive:
                    targets.append((emissive, [glow * c for c in rgb]))
            for target, value in targets:
                target.ClearConnections()
                target.Set(Gf.Vec3f(*[float(c) for c in value]))
            painted += 1
    return painted


def restore_colours(stage, saved):
    """Undo override_colours: rebind each painted primitive to the material it
    had and drop its painted copy; primitives that vanished are skipped."""
    from pxr import Sdf
    layer = stage.GetRootLayer()
    with Sdf.ChangeBlock():
        for path, record in saved.items():
            prim = layer.GetPrimAtPath(path)
            rel = prim.relationships.get('material:binding') if prim else None
            if rel is None or record['material'] == record['binding']:
                continue
            if layer.GetPrimAtPath(record['binding']):
                rel.targetPathList.explicitItems = [Sdf.Path(record['binding'])]
                if layer.GetPrimAtPath(record['material']):
                    _remove_spec(layer, record['material'])
    saved.clear()


def author_scene(stage, packet, texture_dir, asset_prefix=None, validated=False):
    """Build a validated layer; optional relative asset prefix makes disk exports portable.

    Kit's in-memory stages retain absolute cache references by default. The CLI
    writes textures beside the exported stage and supplies its relative prefix.
    validated=True only for a packet SceneTransfer.finish() has just validated:
    the pure-Python walk over every vertex is a large share of the Kit-side
    load, and running it twice doubled that.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
    if not validated:
        validate_snapshot(packet)
    texture_dir = Path(texture_dir)
    if asset_prefix is not None and Path(asset_prefix).is_absolute():
        raise ValueError('Export asset prefix must be relative to the USD file')
    assets, alpha_texture_keys = _texture_assets(packet['textures'], texture_dir, asset_prefix)
    # Publish once per scene, including opaque scenes whose later view may
    # request a translucent grid. Materials still opt in only when needed.
    mdl_asset = _publish_fractional_mdl(texture_dir, asset_prefix)
    work = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(work, '/World')
    root = UsdGeom.Xform.Define(work, ROOT).GetPrim()
    root.SetCustomDataByKey('dcdtuni:projectUid', packet['project']['uid'])
    root.SetCustomDataByKey('dcdtuni:schema', packet['schema'])
    root.SetCustomDataByKey('dcdtuni:fractionalMaterialAsset', mdl_asset)
    # A fresh streaming stage has no scene lights until the old demo loads.
    # The manifest's Kit rig (textures/materials.json lighting.kit), the same
    # one the Kit-native .cfdproj path uses, makes this path renderable alone.
    author_kit_lights(work, ROOT + '/Lighting')
    paths = {}
    room_shell_paths = []
    # One Looks material per distinct (material, vertex colours) pair, shared
    # by every primitive that draws with it: Shinaide's 2,419 primitives use 72.
    # Each material is an RTX/MDL compile, so per-primitive copies were most of
    # Kit's scene build. override_colours copies one out before painting it.
    shared = {}
    # Geometry is written as Sdf specs in one change block: through the Usd
    # API every Define and attribute recomposed the prim, and that was most of
    # Kit's scene build (2.9 s of 3.5 s offline for Shinaide's 2,419
    # primitives). The specs are exactly what the Usd schema calls authored.
    layer = work.GetRootLayer()
    looks = {}
    with Sdf.ChangeBlock():
        geometry_root = Sdf.CreatePrimInLayer(layer, ROOT + '/Geometry')
        geometry_root.specifier = Sdf.SpecifierDef
        for p in packet['primitives']:
            name = 'p_' + hashlib.sha256(p['id'].encode()).hexdigest()[:24]
            path = ROOT + '/Geometry/' + name
            # Calibrated to show what Three shows behind it. See-through sheets
            # and the room shell cast no shadow: a 0.6 sheet's own shadow halved
            # what showed below it (0.085 against 0.166).
            look_spec = kit_look(p['material'])
            key = json.dumps([look_spec, bool(p.get('colors'))], sort_keys=True)
            if key not in looks:
                look = ROOT + '/Looks/' + SHARED_LOOK_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:24]
                looks[key] = (look, look_spec, bool(p.get('colors')))
            _geometry_spec(layer, path, p, looks[key][0])
            if p.get('displayRole') == 'room-shell':
                room_shell_paths.append(path)
            paths.setdefault(p['modelUid'], []).append(path)
    for key, (look, look_spec, has_colors) in looks.items():
        shared[key] = _material(work, look, look_spec, assets, has_colors, alpha_texture_keys, mdl_asset)
    # All input/authoring errors above leave the old stage untouched. Save only
    # the paths this operation can change, then roll them back if any live-stage
    # operation fails. The overlay is cleared only after the new scene and view
    # have both been authored successfully.
    root_layer = stage.GetRootLayer()
    previous_target = stage.GetEditTarget()
    backup_stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(backup_stage, '/World')
    backup_layer = backup_stage.GetRootLayer()
    protected = (ROOT, CAMERA, OVERLAY_ROOT, RESULT_ROOT)
    saved = set()
    world_existed = bool(root_layer.GetPrimAtPath('/World'))
    for path in protected:
        if root_layer.GetPrimAtPath(path):
            if not Sdf.CopySpec(root_layer, path, backup_layer, path):
                raise RuntimeError('Could not preserve previous render scene')
            saved.add(path)
    pseudo = root_layer.pseudoRoot
    metadata = {key: (pseudo.HasInfo(key), pseudo.GetInfo(key))
                for key in ('upAxis', 'metersPerUnit')}
    stage.SetEditTarget(root_layer)
    try:
        UsdGeom.Xform.Define(stage, '/World')
        if root_layer.GetPrimAtPath(ROOT):
            if not stage.RemovePrim(ROOT):
                raise RuntimeError('Could not remove previous render scene')
        if not Sdf.CopySpec(work.GetRootLayer(), ROOT, root_layer, ROOT):
            raise RuntimeError('Could not replace render scene')
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        retired_lights = retire_foreign_lights(stage, ROOT + '/Lighting')
        apply_view(stage, packet['view'])
        if root_layer.GetPrimAtPath(OVERLAY_ROOT):
            if not stage.RemovePrim(OVERLAY_ROOT):
                raise RuntimeError('Could not clear previous render overlay')
        # A replacement project/geometry revision invalidates any derived CFD
        # display. React may resend its independently verified result packet.
        if root_layer.GetPrimAtPath(RESULT_ROOT):
            if not stage.RemovePrim(RESULT_ROOT):
                raise RuntimeError('Could not clear previous render result layers')
    except BaseException:
        for path in protected:
            if root_layer.GetPrimAtPath(path):
                stage.RemovePrim(path)
            if path in saved and not Sdf.CopySpec(backup_layer, path, root_layer, path):
                raise RuntimeError(f'Could not restore previous stage path {path}')
        if not world_existed and root_layer.GetPrimAtPath('/World'):
            stage.RemovePrim('/World')
        for key, (had_value, value) in metadata.items():
            if had_value:
                pseudo.SetInfo(key, value)
            else:
                pseudo.ClearInfo(key)
        raise
    finally:
        stage.SetEditTarget(previous_target)
    from pxr import UsdLux
    lights = sorted(str(prim.GetPath()) for prim in stage.Traverse() if prim.HasAPI(UsdLux.LightAPI))
    return {'uidPaths': paths, 'roomShellPaths': room_shell_paths, 'primitives': len(packet['primitives']),
            'view': packet['view'], 'lights': lights, 'retiredLights': retired_lights,
            'textureAssets': sorted(set(assets.values()))}


def _background_grid_intent_json(grid):
    return json.dumps(grid, sort_keys=True, separators=(',', ':'))


def _owned_background_grid_matches(stage, grid):
    """Reuse only this stage's successfully authored intent and actual carrier."""
    from pxr import Gf, Sdf, UsdGeom
    parent = stage.GetPrimAtPath(BACKGROUND_GRID_ROOT)
    if not parent or parent.GetTypeName() != 'Xform' or parent.GetCustomDataByKey('dcdtuni:viewDecoration') is not True:
        return False
    if parent.GetCustomDataByKey('dcdtuni:backgroundGridIntentJson') != _background_grid_intent_json(grid):
        return False
    ops = UsdGeom.Xformable(parent).GetOrderedXformOps()
    if len(ops) != 1 or ops[0].Get() != Gf.Matrix4d(*grid['matrix']):
        return False
    visibility = UsdGeom.Imageable(parent).GetVisibilityAttr().Get()
    if not grid['visible']:
        return visibility == 'invisible' and not parent.GetChildren()
    lines = stage.GetPrimAtPath(BACKGROUND_GRID_ROOT + '/Lines')
    material_path = BACKGROUND_GRID_ROOT + '/Looks/Grid'
    material = stage.GetPrimAtPath(material_path)
    if visibility != 'inherited' or not lines or lines.GetTypeName() != 'BasisCurves' or not material or material.GetTypeName() != 'Material':
        return False
    if lines.GetRelationship('material:binding').GetTargets() != [Sdf.Path(material_path)]:
        return False
    for name in ('Surface', 'VertexColor'):
        shader = stage.GetPrimAtPath(material_path + '/' + name)
        if not shader or shader.GetTypeName() != 'Shader':
            return False
    return True


def _background_grid_stage(grid, mdl_asset=None):
    """Lower the common small view intent, retaining Three GridHelper vertices.

    Float32 conversion and repeated k += step match the real helper; do not
    replace accumulation by multiplication (centre values differ near zero).
    """
    import struct
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
    work = Usd.Stage.CreateInMemory()
    parent = UsdGeom.Xform.Define(work, BACKGROUND_GRID_ROOT).GetPrim()
    parent.SetCustomDataByKey('dcdtuni:viewDecoration', True)
    parent.SetCustomDataByKey('dcdtuni:backgroundGridIntentJson', _background_grid_intent_json(grid))
    UsdGeom.Xformable(parent).AddTransformOp().Set(Gf.Matrix4d(*grid['matrix']))
    if not grid['visible']:
        UsdGeom.Imageable(parent).MakeInvisible()
        return work
    divisions = grid['divisions']
    half = grid['extentM'] / 2
    step = grid['extentM'] / divisions
    k = -half
    points, colors = [], []
    f32 = lambda value: struct.unpack('f', struct.pack('f', value))[0]
    for i in range(divisions + 1):
        points.extend([Gf.Vec3f(-half, 0, f32(k)), Gf.Vec3f(half, 0, f32(k)),
                       Gf.Vec3f(f32(k), 0, -half), Gf.Vec3f(f32(k), 0, half)])
        color = grid['centerColor'] if i == divisions // 2 else grid['lineColor']
        colors.extend([Gf.Vec3f(*color)] * 4)
        k += step
    geo = UsdGeom.BasisCurves.Define(work, BACKGROUND_GRID_ROOT + '/Lines')
    geo.CreateTypeAttr('linear')
    geo.CreateWrapAttr('nonperiodic')
    geo.CreateCurveVertexCountsAttr([2] * (2 * (divisions + 1)))
    geo.CreatePointsAttr(points)
    # USD curves require a physical width; pixel thickness is a renderer concern.
    _set_constant_widths(geo, .001)
    UsdGeom.PrimvarsAPI(geo).CreatePrimvar('displayColor', Sdf.ValueTypeNames.Color3fArray, 'vertex').Set(colors)
    source = {'color': [1, 1, 1], 'emissive': [0, 0, 0], 'opacity': grid['opacity'],
              'roughness': 1, 'metalness': 0, 'doubleSided': True, 'unlit': True}
    # Standalone view stages have no scene-owned cache yet. The installed
    # tracked sibling is a valid source; portable authored scenes use their
    # own cached (possibly relative) asset passed by _apply_background_grid.
    mdl_asset = mdl_asset or str(Path(__file__).with_name('DcdtuniFractionalPreview.mdl'))
    material = _material(work, BACKGROUND_GRID_ROOT + '/Looks/Grid', source, {}, True, mdl_asset=mdl_asset)
    UsdShade.MaterialBindingAPI.Apply(geo.GetPrim()).Bind(material)
    return work


def _apply_background_grid(stage, view, raster_contract=None):
    from pxr import Sdf, Usd
    grid = view.get('backgroundGrid')
    if grid is None and not stage.GetPrimAtPath(BACKGROUND_GRID_ROOT):
        return
    if grid is not None and _owned_background_grid_matches(stage, grid):
        return
    if grid is not None and grid['visible'] and view.get('backgroundGridRaster'):
        from .grid_raster import carrier_matches
        parent = stage.GetPrimAtPath(BACKGROUND_GRID_ROOT)
        key = parent.GetCustomDataByKey('dcdtuni:rasterCarrierKey') if parent else None
        if (key and parent.GetCustomDataByKey('dcdtuni:backgroundGridIntentJson') == _background_grid_intent_json(grid)
                and carrier_matches(stage,key,raster_contract)):
            # Keep the last verified carrier while the same-scene latest view
            # awaits a new delivered projection. Do not rebuild canonical data.
            return
    if grid is not None and grid['visible'] and raster_contract is not None:
        from .grid_raster import NativeCarrierContract, native_carrier_matches
        parent = stage.GetPrimAtPath(BACKGROUND_GRID_ROOT)
        key = parent.GetCustomDataByKey('dcdtuni:nativeGridCarrierKey') if parent else None
        if (type(raster_contract) is NativeCarrierContract and key
            and raster_contract.intent == _background_grid_intent_json(grid)
            and native_carrier_matches(stage,key,raster_contract)):
            # Typed internal native geometry preserves true depth/plane. It
            # cannot satisfy the encoded carrier or authorize source products.
            return
    scene = stage.GetPrimAtPath(ROOT)
    mdl_asset = scene.GetCustomDataByKey('dcdtuni:fractionalMaterialAsset') if scene else None
    work = _background_grid_stage(grid, mdl_asset) if grid is not None else None
    layer = stage.GetRootLayer()
    old = bool(layer.GetPrimAtPath(BACKGROUND_GRID_ROOT))
    backup = Usd.Stage.CreateInMemory() if old else None
    if old:
        Sdf.CreatePrimInLayer(backup.GetRootLayer(), Sdf.Path(BACKGROUND_GRID_ROOT).GetParentPath())
    if old and not Sdf.CopySpec(layer, BACKGROUND_GRID_ROOT, backup.GetRootLayer(), BACKGROUND_GRID_ROOT):
        raise RuntimeError('Could not preserve previous background grid')
    parent_path = ROOT + '/Decorations'
    had_parent = bool(layer.GetPrimAtPath(parent_path))
    try:
        if work and not had_parent:
            from pxr import UsdGeom
            UsdGeom.Xform.Define(stage, parent_path)
        if old and not stage.RemovePrim(BACKGROUND_GRID_ROOT):
            raise RuntimeError('Could not remove previous background grid')
        if work and not Sdf.CopySpec(work.GetRootLayer(), BACKGROUND_GRID_ROOT, layer, BACKGROUND_GRID_ROOT):
            raise RuntimeError('Could not replace background grid')
    except BaseException:
        stage.RemovePrim(BACKGROUND_GRID_ROOT)
        if backup:
            Sdf.CopySpec(backup.GetRootLayer(), BACKGROUND_GRID_ROOT, layer, BACKGROUND_GRID_ROOT)
        if not had_parent:
            stage.RemovePrim(parent_path)
        raise


def apply_view(stage, view, raster_contract=None):
    from pxr import Gf, UsdGeom
    validate_view(view)
    root = stage.GetPrimAtPath(ROOT)
    if not root:
        raise ValueError('No editor render scene loaded')
    _apply_background_grid(stage, view, raster_contract)
    apply_lighting(stage, view.get('lighting'))
    root.SetCustomDataByKey('dcdtuni:viewJson', json.dumps(view))
    if view['timeSeconds'] is not None:
        root.SetCustomDataByKey('dcdtuni:timeSeconds', float(view['timeSeconds']))
    else:
        root.ClearCustomDataByKey('dcdtuni:timeSeconds')
    c = view.get('camera')
    if c:
        cam = UsdGeom.Camera.Define(stage, CAMERA)
        cam.ClearXformOpOrder()
        matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*c['position']), Gf.Vec3d(*c['target']), Gf.Vec3d(*c['up'])).GetInverse()
        cam.AddTransformOp().Set(matrix)
        aperture = 20.955
        cam.CreateVerticalApertureAttr(aperture)
        cam.CreateHorizontalApertureAttr(aperture * c['aspect'])
        cam.CreateFocalLengthAttr(aperture / (2 * math.tan(math.radians(c['fov']) / 2)))
        cam.CreateClippingRangeAttr(Gf.Vec2f(c['near'], c['far']))
    return view


def read_view(stage, selected_paths=()):
    from pxr import Gf, UsdGeom
    root = stage.GetPrimAtPath(ROOT)
    if not root:
        raise ValueError('No editor render scene loaded')
    view = json.loads(root.GetCustomDataByKey('dcdtuni:viewJson'))
    selected = set()
    for path in selected_paths:
        prim = stage.GetPrimAtPath(path)
        while prim and prim.GetPath().pathString != '/':
            uid = prim.GetCustomDataByKey('dcdtuni:modelUid')
            if uid:
                selected.add(uid)
                break
            prim = prim.GetParent()
    view['selectedUids'] = sorted(selected)
    if view.get('camera') and stage.GetPrimAtPath(CAMERA):
        cam = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA))
        matrix = cam.ComputeLocalToWorldTransform(0)
        c = view['camera']
        distance = math.sqrt(sum((c['position'][i]-c['target'][i])**2 for i in range(3)))
        position = matrix.Transform(Gf.Vec3d(0, 0, 0))
        direction = matrix.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()
        c['position'] = list(position)
        c['target'] = list(position + direction * distance)
        c['up'] = list(matrix.TransformDir(Gf.Vec3d(0, 1, 0)).GetNormalized())
    return view


OVERLAY_ROOT = '/World/RenderOverlay'
OVERLAY_SCHEMA_PATH = SCHEMA_PATH.with_name('render-overlay.schema.json')


def validate_overlay(packet):
    """Validate a mesh-preview overlay; meshSnapshotKey is a stat identity, not a content hash."""
    _validate(packet, json.loads(OVERLAY_SCHEMA_PATH.read_text()))
    if not packet['projectUid'].strip():
        raise ValueError('Overlay projectUid must be non-empty')
    source = packet['source']
    mesh_key = source['meshSnapshotKey']
    if mesh_key is None:
        if packet['primitives']:
            raise ValueError('Non-empty overlay requires meshSnapshotKey')
    elif not mesh_key.strip():
        raise ValueError('Overlay meshSnapshotKey must be non-empty')
    seen = set()
    for primitive in packet['primitives']:
        if not primitive['id'].strip() or not primitive['patchName'].strip():
            raise ValueError('Overlay primitive id and patchName must be non-empty')
        if primitive['id'] in seen:
            raise ValueError('Duplicate overlay primitive identity')
        seen.add(primitive['id'])
        vertex_count, remainder = divmod(len(primitive['positions']), 3)
        stride = 3 if primitive['kind'] == 'triangles' else 2
        if remainder or vertex_count < stride:
            raise ValueError('Invalid overlay positions')
        indices = primitive['indices']
        if len(indices) < stride or len(indices) % stride or any(i >= vertex_count for i in indices):
            raise ValueError('Invalid overlay indices')
        if primitive['kind'] == 'triangles' and 'width' in primitive:
            raise ValueError('Overlay triangle width has no meaning')


def _overlay_material(stage, path, source):
    from pxr import Gf, Sdf, UsdShade
    material = UsdShade.Material.Define(stage, path)
    surface = UsdShade.Shader.Define(stage, path + '/Surface')
    surface.CreateIdAttr('UsdPreviewSurface')
    color = Gf.Vec3f(*source['color'])
    black = Gf.Vec3f(0, 0, 0)
    surface.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(black if source['unlit'] else color)
    surface.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(color if source['unlit'] else black)
    surface.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(source['opacity'])
    surface.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.8)
    surface.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(0)
    surface.CreateOutput('surface', Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), 'surface')
    return material


def author_overlay(stage, packet):
    """Replace one validated mesh-preview USD layer without touching scene geometry or view.

    Authoring completes in memory before the live stage changes. If copying the
    prepared layer fails, restore the previous overlay; project geometry and
    camera are never edited. An empty primitive list removes the overlay.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
    validate_overlay(packet)
    scene = stage.GetPrimAtPath(ROOT)
    if not scene:
        raise ValueError('No editor render scene loaded for overlay')
    if scene.GetCustomDataByKey('dcdtuni:projectUid') != packet['projectUid']:
        raise ValueError('Overlay projectUid does not match the editor render scene')

    receipt = {'primitives': len(packet['primitives']), 'paths': {},
               'projectUid': packet['projectUid'], 'source': dict(packet['source'])}
    root_layer = stage.GetRootLayer()
    old_exists = bool(stage.GetPrimAtPath(OVERLAY_ROOT))
    backup = None
    if old_exists:
        backup_stage = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(backup_stage, '/World')
        backup = backup_stage.GetRootLayer()
        if not Sdf.CopySpec(root_layer, OVERLAY_ROOT, backup, OVERLAY_ROOT):
            raise RuntimeError('Could not preserve previous render overlay')

    if packet['primitives']:
        work = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(work, '/World')
        overlay = UsdGeom.Xform.Define(work, OVERLAY_ROOT).GetPrim()
        overlay.SetCustomDataByKey('dcdtuni:projectUid', packet['projectUid'])
        overlay.SetCustomDataByKey('dcdtuni:sourceKind', packet['source']['kind'])
        overlay.SetCustomDataByKey('dcdtuni:meshSnapshotKey', packet['source']['meshSnapshotKey'])
        for primitive in packet['primitives']:
            name = 'p_' + hashlib.sha256(primitive['id'].encode()).hexdigest()[:24]
            path = OVERLAY_ROOT + '/Geometry/' + name
            if primitive['kind'] == 'triangles':
                geometry = UsdGeom.Mesh.Define(work, path)
                geometry.CreatePointsAttr(_tuples(primitive['positions'], 3))
                geometry.CreateFaceVertexIndicesAttr(primitive['indices'])
                geometry.CreateFaceVertexCountsAttr([3] * (len(primitive['indices']) // 3))
                geometry.CreateSubdivisionSchemeAttr('none')
                geometry.CreateDoubleSidedAttr(primitive['material']['doubleSided'])
            else:
                geometry = UsdGeom.BasisCurves.Define(work, path)
                points = _tuples(primitive['positions'], 3)
                geometry.CreateTypeAttr('linear')
                geometry.CreateCurveVertexCountsAttr([2] * (len(primitive['indices']) // 2))
                geometry.CreatePointsAttr([points[index] for index in primitive['indices']])
                _set_constant_widths(geometry, primitive.get('width', .001))
            prim = geometry.GetPrim()
            prim.SetDisplayName(primitive['patchName'])
            prim.SetCustomDataByKey('dcdtuni:primitiveId', primitive['id'])
            prim.SetCustomDataByKey('dcdtuni:patchName', primitive['patchName'])
            prim.SetCustomDataByKey('dcdtuni:sourceKind', packet['source']['kind'])
            prim.SetCustomDataByKey('dcdtuni:meshSnapshotKey', packet['source']['meshSnapshotKey'])
            UsdGeom.Xformable(prim).AddTransformOp().Set(Gf.Matrix4d(*primitive['matrix']))
            if not primitive['visible']:
                UsdGeom.Imageable(prim).MakeInvisible()
            material = _overlay_material(work, OVERLAY_ROOT + '/Looks/' + name,
                                         primitive['material'])
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
            receipt['paths'][primitive['id']] = path

    # Edit only the overlay path. The backup makes a failed copy observable as
    # an unchanged stage, including when an old overlay was already present.
    try:
        if old_exists:
            stage.RemovePrim(OVERLAY_ROOT)
        if packet['primitives'] and not Sdf.CopySpec(work.GetRootLayer(), OVERLAY_ROOT,
                                                     root_layer, OVERLAY_ROOT):
            raise RuntimeError('Could not replace render overlay')
    except BaseException:
        if stage.GetPrimAtPath(OVERLAY_ROOT):
            stage.RemovePrim(OVERLAY_ROOT)
        if backup is not None and not Sdf.CopySpec(backup, OVERLAY_ROOT,
                                                   root_layer, OVERLAY_ROOT):
            raise RuntimeError('Could not restore previous render overlay')
        raise
    return receipt


if __name__ == '__main__':
    import argparse
    from pxr import Usd
    parser = argparse.ArgumentParser(description='Validate a derived editor scene and export USD on CPU (no Kit/RTX).')
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    usd = Usd.Stage.CreateInMemory()
    texture_prefix = args.output.stem + '_textures'
    receipt = author_scene(usd, data, args.output.parent / texture_prefix, asset_prefix=texture_prefix)
    usd.GetRootLayer().Export(str(args.output))
    print(json.dumps({'output': str(args.output), 'models': len(receipt['uidPaths']), 'primitives': receipt['primitives']}))
