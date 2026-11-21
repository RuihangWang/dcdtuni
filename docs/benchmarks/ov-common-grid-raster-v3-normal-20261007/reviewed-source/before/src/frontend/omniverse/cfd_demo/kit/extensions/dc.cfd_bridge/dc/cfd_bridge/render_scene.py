"""Validated editor-render snapshot -> OpenUSD. CPU-only; Kit integration is separate.

This is a derived visual representation, never a cfdproj/cfdrun/liquidrun writer.
The shared JSON Schema is the wire contract. The small validator supports exactly
the keywords that schema uses, without requiring jsonschema inside NVIDIA Kit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import re
import time

SCHEMA_PATH = Path(__file__).resolve().parents[7] / 'shared/schema/render-scene.schema.json'
ROOT = '/World/RenderScene'
CAMERA = '/World/RenderCamera'
RESULT_ROOT = '/World/RenderResultLayers'
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
        if _plain_scalars(items):
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
    for uri in packet['textures'].values():
        texture_bytes(uri)


class SceneTransfer:
    """One bounded, ordered transfer; no unbounded assembly on a shared Kit process."""
    def __init__(self, validator=validate_snapshot):
        self.current = None
        if not callable(validator):
            raise TypeError('Scene transfer validator must be callable')
        self.validator = validator

    def begin(self, request_id, chunks):
        if not isinstance(request_id, str) or not request_id or type(chunks) is not int or not 1 <= chunks <= 16384:
            raise ValueError('Invalid scene transfer header')
        self.current = {'id': request_id, 'count': chunks, 'data': [], 'size': 0, 'at': time.monotonic()}

    def chunk(self, request_id, index, data):
        c = self.current
        if not c or c['id'] != request_id or time.monotonic() - c['at'] > 120:
            raise ValueError('Missing or expired scene transfer')
        if type(index) is not int or index != len(c['data']) or index >= c['count'] or not isinstance(data, str) or len(data) > 16000:
            self.current = None
            raise ValueError('Out-of-order or oversized scene chunk')
        c['size'] += len(data.encode('utf-8'))
        if c['size'] > 128 * 1024 * 1024:
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
        packet = json.loads(''.join(c['data']))
        self.validator(packet)
        return packet


def _tuples(values, n):
    return [tuple(values[i:i+n]) for i in range(0, len(values), n)]


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


def _material(stage, path, source, assets, has_colors):
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


def _surface_colour_attr(stage, geometry_path):
    """The Surface input carrying a primitive's base colour: diffuse, or
    emissive for unlit materials (authored with a black diffuse)."""
    name = geometry_path.rsplit('/', 1)[-1]
    shader = stage.GetPrimAtPath(ROOT + '/Looks/' + name + '/Surface')
    if not shader:
        return None
    diffuse = shader.GetAttribute('inputs:diffuseColor')
    emissive = shader.GetAttribute('inputs:emissiveColor')
    black = diffuse and not diffuse.GetConnections() and tuple(diffuse.Get() or (0, 0, 0)) == (0, 0, 0)
    lit_emissive = emissive and (emissive.GetConnections() or any(emissive.Get() or (0, 0, 0)))
    return emissive if black and lit_emissive else diffuse


def override_colours(stage, colours, saved, glow=0.0):
    """Paint {geometry_path: (r, g, b) in 0..1} onto each primitive's OWN
    material (one Looks material per primitive, so nothing bleeds). The first
    touch of a primitive records every value and connection it changes in
    ``saved`` so restore_colours can undo texture/vertex-colour disconnects
    exactly. ``glow`` > 0 also sets a lit surface's emissive colour to
    glow * rgb, so a result colour stays readable in shadow or under a
    translucent raised floor (unlit surfaces are already emissive).
    Returns the number of primitives painted."""
    from pxr import Gf, Sdf
    painted = 0
    with Sdf.ChangeBlock():
        for path, rgb in colours.items():
            attr = _surface_colour_attr(stage, path)
            if not attr:
                continue
            targets = [(attr, rgb)]
            if glow > 0 and attr.GetName() == 'inputs:diffuseColor':
                emissive = attr.GetPrim().GetAttribute('inputs:emissiveColor')
                if emissive:
                    targets.append((emissive, [glow * c for c in rgb]))
            record = saved.setdefault(path, {})
            for target, value in targets:
                key = str(target.GetPath())
                if key not in record:
                    record[key] = (target.GetPath(), target.Get(), list(target.GetConnections()))
                target.ClearConnections()
                target.Set(Gf.Vec3f(*[float(c) for c in value]))
            painted += 1
    return painted


def restore_colours(stage, saved):
    """Undo override_colours; paths whose material vanished are skipped."""
    from pxr import Sdf
    with Sdf.ChangeBlock():
        for record in saved.values():
            for attr_path, value, connections in record.values():
                attr = stage.GetAttributeAtPath(attr_path)
                if not attr:
                    continue
                if value is not None:
                    attr.Set(value)
                if connections:
                    attr.SetConnections(connections)
    saved.clear()


def author_scene(stage, packet, texture_dir, asset_prefix=None, validated=False):
    """Build a validated layer; optional relative asset prefix makes disk exports portable.

    Kit's in-memory stages retain absolute cache references by default. The CLI
    writes textures beside the exported stage and supplies its relative prefix.
    validated=True only for a packet SceneTransfer.finish() has just validated:
    the pure-Python walk over every vertex is a large share of the Kit-side
    load, and running it twice doubled that.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdLux
    if not validated:
        validate_snapshot(packet)
    texture_dir = Path(texture_dir)
    if asset_prefix is not None and Path(asset_prefix).is_absolute():
        raise ValueError('Export asset prefix must be relative to the USD file')
    assets = {}
    for key, uri in packet['textures'].items():
        data, suffix = texture_bytes(uri)
        texture_dir.mkdir(parents=True, exist_ok=True)
        file = texture_dir / (hashlib.sha256(data).hexdigest() + suffix)
        if not file.exists():
            file.write_bytes(data)
        assets[key] = ((Path(asset_prefix) / file.name).as_posix()
                       if asset_prefix is not None else str(file.resolve()))
    work = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(work, '/World')
    root = UsdGeom.Xform.Define(work, ROOT).GetPrim()
    root.SetCustomDataByKey('dcdtuni:projectUid', packet['project']['uid'])
    root.SetCustomDataByKey('dcdtuni:schema', packet['schema'])
    # A fresh streaming stage has no scene lights until the old demo loads.
    # A modest neutral rig makes this independent path renderable on its own.
    dome = UsdLux.DomeLight.Define(work, ROOT + '/Lighting/Dome')
    dome.CreateIntensityAttr(250)
    key = UsdLux.DistantLight.Define(work, ROOT + '/Lighting/Key')
    key.CreateIntensityAttr(1000)
    UsdGeom.Xformable(key).AddRotateXYZOp().Set(Gf.Vec3f(-45, 35, 0))
    paths = {}
    room_shell_paths = []
    for p in packet['primitives']:
        name = 'p_' + hashlib.sha256(p['id'].encode()).hexdigest()[:24]
        path = ROOT + '/Geometry/' + name
        if p['kind'] == 'triangles':
            geo = UsdGeom.Mesh.Define(work, path)
            geo.CreatePointsAttr(_tuples(p['positions'], 3))
            geo.CreateFaceVertexIndicesAttr(p['indices'])
            geo.CreateFaceVertexCountsAttr([3] * (len(p['indices']) // 3))
            geo.CreateSubdivisionSchemeAttr('none')
            if 'normals' in p:
                geo.CreateNormalsAttr(_tuples(p['normals'], 3))
                geo.SetNormalsInterpolation('vertex')
            if 'uvs' in p:
                UsdGeom.PrimvarsAPI(geo).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set(_tuples(p['uvs'], 2))
            if 'colors' in p:
                UsdGeom.PrimvarsAPI(geo).CreatePrimvar('displayColor', Sdf.ValueTypeNames.Color3fArray, 'vertex').Set(_tuples(p['colors'], 3))
        else:
            points = _tuples(p['positions'], 3)
            selected = [points[i] for i in p['indices']]
            if p['kind'] == 'lines':
                geo = UsdGeom.BasisCurves.Define(work, path)
                geo.CreateTypeAttr('linear')
                geo.CreateCurveVertexCountsAttr([2] * (len(selected) // 2))
            else:
                geo = UsdGeom.Points.Define(work, path)
            geo.CreatePointsAttr(selected)
            _set_constant_widths(geo, p.get('width', .001))
            if 'colors' in p:
                colors = _tuples(p['colors'], 3)
                UsdGeom.PrimvarsAPI(geo).CreatePrimvar('displayColor', Sdf.ValueTypeNames.Color3fArray, 'vertex').Set([colors[i] for i in p['indices']])
            if 'uvs' in p:
                uvs = _tuples(p['uvs'], 2)
                UsdGeom.PrimvarsAPI(geo).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set([uvs[i] for i in p['indices']])
        prim = geo.GetPrim()
        prim.SetDisplayName(p['name'])
        prim.SetCustomDataByKey('dcdtuni:modelUid', p['modelUid'])
        prim.SetCustomDataByKey('dcdtuni:primitiveId', p['id'])
        if p.get('displayRole') == 'room-shell':
            prim.SetCustomDataByKey('dcdtuni:displayRole', 'room-shell')
            room_shell_paths.append(path)
        if p.get('equipmentUid'):
            prim.SetCustomDataByKey('dcdtuni:equipmentUid', p['equipmentUid'])
        if p.get('liquidPart'):
            prim.SetCustomDataByKey('dcdtuni:liquidPart', p['liquidPart'])
        UsdGeom.Xformable(prim).AddTransformOp().Set(Gf.Matrix4d(*p['matrix']))
        geo.CreateDoubleSidedAttr(p['material']['doubleSided'])
        if not p['visible']:
            UsdGeom.Imageable(prim).MakeInvisible()
        material = _material(work, ROOT + '/Looks/' + name, p['material'], assets, bool(p.get('colors')))
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
        paths.setdefault(p['modelUid'], []).append(path)
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
    return {'uidPaths': paths, 'roomShellPaths': room_shell_paths, 'primitives': len(packet['primitives']), 'view': packet['view']}


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


def _background_grid_stage(grid):
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
    material = _material(work, BACKGROUND_GRID_ROOT + '/Looks/Grid', source, {}, True)
    UsdShade.MaterialBindingAPI.Apply(geo.GetPrim()).Bind(material)
    return work


def _apply_background_grid(stage, view):
    from pxr import Sdf, Usd
    grid = view.get('backgroundGrid')
    if grid is None and not stage.GetPrimAtPath(BACKGROUND_GRID_ROOT):
        return
    if grid is not None and _owned_background_grid_matches(stage, grid):
        return
    work = _background_grid_stage(grid) if grid is not None else None
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


def apply_view(stage, view):
    from pxr import Gf, UsdGeom
    validate_view(view)
    root = stage.GetPrimAtPath(ROOT)
    if not root:
        raise ValueError('No editor render scene loaded')
    _apply_background_grid(stage, view)
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
