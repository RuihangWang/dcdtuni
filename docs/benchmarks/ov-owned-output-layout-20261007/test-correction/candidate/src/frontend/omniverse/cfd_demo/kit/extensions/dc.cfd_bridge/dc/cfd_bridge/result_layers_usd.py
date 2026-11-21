"""Author validated renderer-neutral result layers as OpenUSD, on CPU only.

This writes a *derived* layer under /World/RenderResultLayers. It never edits
the model scene, the .cfdproj, or a solver input. A trusted caller must supply
the current run/mesh identity and physical time; the packet cannot certify its
own freshness. This module is intentionally not wired to a Kit command yet.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA_PATH = Path(__file__).resolve().parents[7] / 'shared/schema/render-result-layers.schema.json'
ROOT = '/World/RenderResultLayers'
EDGE_WIDTH_METERS = .001


@lru_cache(maxsize=1)
def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def _schema_validate(value, rule, path='$'):
    """The exact JSON-Schema V1 keywords, without a Kit package dependency."""
    if '$ref' in rule:
        ref = rule['$ref']
        if not ref.startswith('#/$defs/'):
            raise ValueError(f'{path}: unsupported result-layer schema reference')
        return _schema_validate(value, _schema()['$defs'][ref.removeprefix('#/$defs/')], path)
    if 'oneOf' in rule:
        matches = 0
        for option in rule['oneOf']:
            try:
                _schema_validate(value, option, path)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError(f'{path}: result-layer schema oneOf mismatch')
    types = rule.get('type', [])
    if isinstance(types, str):
        types = [types]
    checks = {
        'object': lambda v: isinstance(v, dict),
        'array': lambda v: isinstance(v, list),
        'string': lambda v: isinstance(v, str),
        'boolean': lambda v: type(v) is bool,
        'number': lambda v: type(v) in (float, int) and math.isfinite(v),
        'integer': lambda v: type(v) is int,
        'null': lambda v: v is None,
    }
    if types and not any(checks[t](value) for t in types):
        raise ValueError(f'{path}: result-layer schema type mismatch')
    if 'const' in rule and (value != rule['const'] or
                            (type(value) is bool) != (type(rule['const']) is bool)):
        raise ValueError(f'{path}: wrong schema constant')
    if 'enum' in rule and value not in rule['enum']:
        raise ValueError(f'{path}: unsupported schema value')
    if isinstance(value, dict):
        properties = rule.get('properties', {})
        for key in rule.get('required', []):
            if key not in value:
                raise ValueError(f'{path}: missing {key}')
        for key, child in value.items():
            if key in properties:
                _schema_validate(child, properties[key], f'{path}.{key}')
            elif rule.get('additionalProperties') is False:
                raise ValueError(f'{path}: unknown {key}')
    elif isinstance(value, list):
        if len(value) < rule.get('minItems', 0) or len(value) > rule.get('maxItems', math.inf):
            raise ValueError(f'{path}: wrong array length')
        if rule.get('uniqueItems') and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError(f'{path}: duplicate array item')
        if 'items' in rule:
            for index, child in enumerate(value):
                _schema_validate(child, rule['items'], f'{path}[{index}]')
    elif isinstance(value, str):
        if len(value) < rule.get('minLength', 0):
            raise ValueError(f'{path}: empty string')
        if 'pattern' in rule and not re.search(rule['pattern'], value):
            raise ValueError(f'{path}: malformed identity')
    elif type(value) in (int, float):
        for name, invalid in (
            ('minimum', lambda bound: value < bound),
            ('maximum', lambda bound: value > bound),
            ('exclusiveMinimum', lambda bound: value <= bound),
            ('exclusiveMaximum', lambda bound: value >= bound),
        ):
            if name in rule and invalid(rule[name]):
                raise ValueError(f'{path}: outside {name}')


def _positions(values, description, *, min_points=1):
    if len(values) < 3 * min_points or len(values) % 3:
        raise ValueError(f'{description}: invalid xyz point span')
    return len(values) // 3


def _indices(values, points, arity, description):
    if not values or len(values) % arity or any(index >= points for index in values):
        raise ValueError(f'{description}: invalid index span')


def validate_result_packet(packet):
    """Apply the shared V1 schema and its cross-field semantics before USD."""
    _schema_validate(packet, _schema())
    source = packet['source']
    if not source['projectUid'].strip():
        raise ValueError('Missing result source identity: projectUid')
    if source['run'] is not None and not source['run']['uid'].strip():
        raise ValueError('Missing result run identity')
    if source['run'] is None and packet['timeSeconds'] is not None:
        raise ValueError('Mesh-only result layer has no physical time')

    seen_ids = set()
    patch_sources = {}
    for layer in packet['layers']:
        layer_id = layer['layerId']
        if not layer_id.strip() or layer_id in seen_ids:
            raise ValueError(f'Duplicate or empty result layer ID: {layer_id!r}')
        seen_ids.add(layer_id)
        kind = layer['kind']
        if kind in ('meshEdges', 'meshFaces', 'surfaceField'):
            patch = layer['meshPatchName']
            if not patch.strip():
                raise ValueError('Missing meshPatchName')
            if packet['schema'] == 'dcdtuni.render-result-layers/v1':
                binding = layer['sourceFaceUids']
                if any(not uid.strip() for uid in binding) or binding != sorted(binding):
                    raise ValueError(f'Mesh patch {patch} sourceFaceUids must be nonempty, sorted and unique')
            else:
                binding = layer['boundaryProvenance']
                seen_ranges = []
                for span in binding['patchRanges']:
                    address = (span['rank'], span['patchIndex'])
                    if seen_ranges and address <= seen_ranges[-1]:
                        raise ValueError('Native boundary ranges must be ordered and unique')
                    seen_ranges.append(address)
                    inventory = span['sourceRegions']
                    if bool(inventory) != (span['bindingKind'] == 'regionFacetInventory'):
                        raise ValueError('Native boundary inventory kind differs from evidence')
                    uids = [row['sourceRegionUid'] for row in inventory]
                    if uids != sorted(set(uids)):
                        raise ValueError('Original source region inventory is not ordered/unique')
                    for row in inventory:
                        if row['sourceFacetUidPrefix'] != row['sourceRegionUid'] + '/facet/':
                            raise ValueError('Original facet namespace differs from region inventory')
            previous = patch_sources.setdefault(patch, binding)
            if previous != binding:
                raise ValueError(f'Mesh patch {patch} boundary provenance mismatch')
            count = _positions(layer['positions'], layer_id)
            _indices(layer['indices'], count, 2 if kind == 'meshEdges' else 3, layer_id)
        elif kind == 'slice':
            count = _positions(layer['positions'], layer_id)
            _indices(layer['indices'], count, 3, layer_id)
            if math.hypot(*layer['normal']) <= 0:
                raise ValueError(f'{layer_id}: invalid slice normal')
        if kind in ('meshEdges','meshFaces'):
            continue
        if source['run'] is None or packet['timeSeconds'] is None:
            raise ValueError('Scalar result layers require run receipt and physical time')
        if source['meshQuality'] != 'accepted':
            raise ValueError('Scalar result layers require accepted mesh quality')
        if not layer['field']['name'].strip() or not layer['field']['unit'].strip():
            raise ValueError(f'{layer_id}: missing field name or unit')
        low, high = layer['lut']['range']
        if low > high:
            raise ValueError(f'{layer_id}: LUT range must not descend')
        if kind in ('surfaceField', 'slice'):
            if len(layer['values']) != count:
                raise ValueError(f'{layer_id}: scalar count differs from mesh points')
        else:
            if not layer['sourceSeedUid'].strip():
                raise ValueError(f'{layer_id}: missing streamline seed UID')
            for index, line in enumerate(layer['lines']):
                n_points = _positions(line['positions'], f'{layer_id} line {index}', min_points=2)
                if len(line['values']) != n_points:
                    raise ValueError(f'{layer_id} line {index}: scalar count differs from path points')
    if packet['schema'] == 'dcdtuni.render-result-layers/v3':
        volume=packet['volume'];grid=volume['grid']
        if source['run'] is None or packet['timeSeconds'] is None or source['meshQuality']!='accepted':
            raise ValueError('Volume requires accepted source run/physical frame')
        if volume['field']!=grid['field'] or volume['artifactName']!=grid['payloadSha256']+'.volume':
            raise ValueError('Volume field/artifact differs from actual grid')
        if len(set(grid['dimensions']))!=1 or grid['scalarOffset']%4 or grid['validityMask']['offset']!=grid['scalarOffset']+4*math.prod(grid['dimensions']) or grid['validityMask']['byteCount']!=math.prod(grid['dimensions']) or grid['payloadBytes']!=grid['validityMask']['offset']+grid['validityMask']['byteCount']:
            raise ValueError('Volume dimensions/offset differ from actual payload bytes')
        if grid['scalarRange'][0]>grid['scalarRange'][1]or volume['lut']['range'][0]>volume['lut']['range'][1]:
            raise ValueError('Volume scalar/LUT range is invalid')
    return packet


def validate_result_transfer(value):
    """A browser transfer carries a separate, current publisher selection."""
    if not isinstance(value, dict) or set(value) != {'packet', 'expectedSource', 'expectedTimeSeconds'}:
        raise ValueError('Result transfer requires packet and current selection')
    validate_result_packet(value['packet'])
    source = value['expectedSource']
    if not isinstance(source, dict) or source != value['packet']['source']:
        raise ValueError('Result transfer source differs from current selection')
    expected_time = value['expectedTimeSeconds']
    if (expected_time is not None and
            (type(expected_time) not in (int, float) or not math.isfinite(expected_time))):
        raise ValueError('Invalid current result time')
    if type(expected_time) is not type(value['packet']['timeSeconds']) and not (
            type(expected_time) in (int, float) and
            type(value['packet']['timeSeconds']) in (int, float)):
        raise ValueError('Result transfer time type differs from current selection')
    if expected_time != value['packet']['timeSeconds']:
        raise ValueError('Result transfer physical time differs from current selection')
    return value


def require_trusted_result_context(transfer, trusted_context):
    """Production Kit must have a source attested outside the WebRTC packet.

    Browser-provided expectedSource/time are stale-read guards, not evidence.
    The current backend issues no complete lineage certificate, so Kit starts
    with ``None`` and rejects every live result-layer command. An eventual
    server-side verification path may set a context only after checking the
    final mesh, run, field dimensions and project identity independently.
    """
    validate_result_transfer(transfer)
    if not isinstance(trusted_context, dict):
        raise ValueError('Trusted renderer provenance is unavailable')
    if set(trusted_context) != {'source', 'timeSeconds'} or (
            trusted_context['source'] != transfer['packet']['source'] or
            trusted_context['timeSeconds'] != transfer['packet']['timeSeconds']):
        raise ValueError('Result transfer differs from trusted renderer provenance')


def require_acknowledged_scene_hash(source, scene_project_hash):
    """An old result cannot attach to a newly ACKed scene with the same UID."""
    if not isinstance(scene_project_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', scene_project_hash):
        raise ValueError('ACKed scene has no project content hash')
    if source['projectContentSha256'] != scene_project_hash:
        raise ValueError('Result project content hash differs from ACKed scene')


def _source_json(source):
    return json.dumps(source, sort_keys=True, separators=(',', ':'))


def _assert_current(stage, packet, expected_source, expected_time_seconds, last_revision):
    if not isinstance(expected_source, dict):
        raise ValueError('Missing current result source identity')
    source = packet['source']
    for key in ('projectUid', 'projectContentSha256', 'cfdrunSha256',
                'meshReceiptSha256', 'meshQuality', 'run'):
        if key not in expected_source or source[key] != expected_source[key]:
            raise ValueError(f'Result layer {key} does not match current source')
    if packet['timeSeconds'] != expected_time_seconds:
        raise ValueError('Result layer physical time does not match current sample')
    if last_revision is not None:
        if type(last_revision) is not int or last_revision < 0:
            raise ValueError('Invalid previous result layer revision')
        if packet['revision'] <= last_revision:
            raise ValueError('Stale result layer revision')
    root = stage.GetPrimAtPath(ROOT)
    if root and root.GetCustomDataByKey('dcdtuni:sourceJson') == _source_json(source):
        prior = root.GetCustomDataByKey('dcdtuni:revision')
        if type(prior) is int and packet['revision'] <= prior:
            raise ValueError('Stale result layer revision')


@lru_cache(maxsize=3)
def _lut_bytes(palette):
    """Match the 256-entry JS ppColormaps table, including byte rounding."""
    def byte(x):
        return math.floor(x + .5)

    table = []
    stops = ((0, 68, 1, 84), (.25, 59, 82, 139),
             (.5, 33, 145, 140), (.75, 94, 201, 98), (1, 253, 231, 37))
    for i in range(256):
        t = i / 255
        if palette == 'jet':
            rgb = tuple(byte(255 * min(1, max(0, 1.5 - abs(4 * t - centre))))
                        for centre in (3, 2, 1))
        elif palette == 'viridis':
            lo, hi = stops[-2:]
            for j in range(1, len(stops)):
                if t <= stops[j][0]:
                    lo, hi = stops[j - 1], stops[j]
                    break
            f = (t - lo[0]) / (hi[0] - lo[0])
            rgb = tuple(byte(lo[c] + f * (hi[c] - lo[c])) for c in (1, 2, 3))
        elif palette == 'coolwarm':
            if t < .5:
                s = 2 * t
                rgb = (byte(59 + s * 196), byte(76 + s * 179), byte(192 + s * 63))
            else:
                s = (t - .5) * 2
                rgb = (byte(255 - s * 75), byte(255 - s * 251), byte(255 - s * 217))
        else:
            raise ValueError(f'Unsupported LUT: {palette}')
        table.append(rgb)
    return table


def _colors(values, lut):
    from pxr import Gf
    lo, hi = lut['range']
    table = _lut_bytes(lut['palette'])
    result = []
    for value in values:
        # A zero-width display LUT maps every sample to the first palette
        # entry. The original scalar/domain remain untouched, including outliers.
        t = 0. if hi == lo else min(1, max(0, (value - lo) / (hi - lo)))
        rgb = table[math.floor(t * 255 + .5)]
        result.append(Gf.Vec3f(*(channel / 255 for channel in rgb)))
    return result


def _bind_material(stage, path, prim, opacity, *, vertex_colors,color=None,unlit=False):
    from pxr import Gf, Sdf, UsdShade
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + '/Surface')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(float(opacity))
    shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(1.)
    if vertex_colors:
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0, 0, 0))
        reader = UsdShade.Shader.Define(stage, path + '/VertexColor')
        reader.CreateIdAttr('UsdPrimvarReader_float3')
        reader.CreateInput('varname', Sdf.ValueTypeNames.String).Set('displayColor')
        reader.CreateOutput('result', Sdf.ValueTypeNames.Float3)
        shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(reader.ConnectableAPI(), 'result')
    else:
        rgb=Gf.Vec3f(*(color or [.7,.7,.7]))
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0,0,0)if unlit else rgb)
        if unlit:shader.CreateInput('emissiveColor',Sdf.ValueTypeNames.Color3f).Set(rgb)
    shader.CreateOutput('surface', Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)


def _author_one(stage, layer, path):
    from pxr import Sdf, UsdGeom, Vt
    kind = layer['kind']
    if kind == 'meshEdges':
        points = [tuple(layer['positions'][i:i + 3]) for i in range(0, len(layer['positions']), 3)]
        geo = UsdGeom.BasisCurves.Define(stage, path)
        geo.CreateTypeAttr('linear')
        geo.CreateCurveVertexCountsAttr([2] * (len(layer['indices']) // 2))
        geo.CreatePointsAttr([points[i] for i in layer['indices']])
        geo.CreateWidthsAttr([EDGE_WIDTH_METERS])
        opacity = layer['opacity']
    elif kind in ('surfaceField', 'slice','meshFaces'):
        geo = UsdGeom.Mesh.Define(stage, path)
        geo.CreatePointsAttr([tuple(layer['positions'][i:i + 3])
                              for i in range(0, len(layer['positions']), 3)])
        geo.CreateFaceVertexCountsAttr([3] * (len(layer['indices']) // 3))
        geo.CreateFaceVertexIndicesAttr(layer['indices'])
        geo.CreateSubdivisionSchemeAttr('none')
        geo.CreateDoubleSidedAttr(True)
        if kind=='meshFaces':opacity=layer['opacity']
        else:
            opacity = layer['lut']['opacity']
            values = layer['values']
    else:
        geo = UsdGeom.BasisCurves.Define(stage, path)
        geo.CreateTypeAttr('linear')
        geo.CreateCurveVertexCountsAttr([len(line['values']) for line in layer['lines']])
        geo.CreatePointsAttr([tuple(line['positions'][i:i + 3])
                              for line in layer['lines']
                              for i in range(0, len(line['positions']), 3)])
        geo.CreateWidthsAttr([2 * layer['tubeRadiusMeters']])
        opacity = layer['lut']['opacity']
        values = [value for line in layer['lines'] for value in line['values']]
    prim = geo.GetPrim()
    prim.SetDisplayName(layer['layerId'])
    prim.SetCustomDataByKey('dcdtuni:kind', kind)
    prim.SetCustomDataByKey('dcdtuni:layerId', layer['layerId'])
    if kind == 'streamline':
        prim.SetCustomDataByKey('dcdtuni:sourceSeedUid', layer['sourceSeedUid'])
    elif kind == 'slice':
        prim.SetCustomDataByKey('dcdtuni:sourceSliceUid', layer['sourceSliceUid'])
        prim.SetCustomDataByKey('dcdtuni:originMeters', Vt.DoubleArray(layer['originMeters']))
        prim.SetCustomDataByKey('dcdtuni:normal', Vt.DoubleArray(layer['normal']))
    else:
        prim.SetCustomDataByKey('dcdtuni:meshPatchName', layer['meshPatchName'])
        if 'sourceFaceUids' in layer:
            prim.SetCustomDataByKey('dcdtuni:sourceFaceUids', Vt.StringArray(layer['sourceFaceUids']))
        else:
            prim.SetCustomDataByKey('dcdtuni:boundaryProvenanceJson',
                                    json.dumps(layer['boundaryProvenance'], sort_keys=True, separators=(',', ':')))
    if kind not in ('meshEdges','meshFaces'):
        prim.SetCustomDataByKey('dcdtuni:fieldName', layer['field']['name'])
        prim.SetCustomDataByKey('dcdtuni:fieldUnit', layer['field']['unit'])
        prim.SetCustomDataByKey('dcdtuni:lutPalette', layer['lut']['palette'])
        prim.SetCustomDataByKey('dcdtuni:lutRange', Vt.DoubleArray(layer['lut']['range']))
        UsdGeom.PrimvarsAPI(geo).CreatePrimvar(
            'dcdtuniRawScalar', Sdf.ValueTypeNames.DoubleArray, 'vertex').Set(values)
        UsdGeom.PrimvarsAPI(geo).CreatePrimvar(
            'displayColor', Sdf.ValueTypeNames.Color3fArray, 'vertex').Set(
                _colors(values, layer['lut']))
    if not layer['visible']:
        UsdGeom.Imageable(prim).MakeInvisible()
    _bind_material(stage, path.replace('/Layers/', '/Looks/'), prim, opacity,
                   vertex_colors=kind not in ('meshEdges','meshFaces'),color=layer.get('color'),unlit=kind=='meshEdges'and 'color'in layer)
    return prim


def author_result_layers(stage, packet, *, expected_source, expected_time_seconds,
                         last_revision=None, commit_guard=None, volume_carrier=None):
    """Replace only the derived USD result subtree after strict CPU validation.

    ``expected_source`` and ``expected_time_seconds`` come from the currently
    selected project/run/mesh, never from the packet itself in production.
    ``last_revision`` is the receiver's independent monotone sequence state.
    """
    from pxr import Sdf, Usd, UsdGeom
    validate_result_packet(packet)
    _assert_current(stage, packet, expected_source, expected_time_seconds, last_revision)
    work = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(work, '/World')
    root = UsdGeom.Xform.Define(work, ROOT).GetPrim()
    source = packet['source']
    root.SetCustomDataByKey('dcdtuni:schema', packet['schema'])
    root.SetCustomDataByKey('dcdtuni:revision', packet['revision'])
    root.SetCustomDataByKey('dcdtuni:sourceJson', _source_json(source))
    if packet['schema'] in ('dcdtuni.render-result-layers/v2','dcdtuni.render-result-layers/v3'):
        root.SetCustomDataByKey('dcdtuni:runConvergenceJson', json.dumps(packet['runConvergence'], sort_keys=True))
    if packet['schema'] == 'dcdtuni.render-result-layers/v4':
        root.SetCustomDataByKey('dcdtuni:meshPreviewJson', json.dumps(packet['meshPreview'], sort_keys=True))
    for key in ('projectUid', 'projectContentSha256', 'cfdrunSha256',
                'meshReceiptSha256', 'meshQuality'):
        root.SetCustomDataByKey('dcdtuni:' + key, source[key])
    if source['run'] is not None:
        root.SetCustomDataByKey('dcdtuni:runUid', source['run']['uid'])
        root.SetCustomDataByKey('dcdtuni:runReceiptSha256', source['run']['receiptSha256'])
    if packet['timeSeconds'] is not None:
        root.SetCustomDataByKey('dcdtuni:timeSeconds', float(packet['timeSeconds']))
    paths = {}
    for layer in packet['layers']:
        name = 'p_' + hashlib.sha256(layer['layerId'].encode()).hexdigest()[:24]
        path = ROOT + '/Layers/' + name
        _author_one(work, layer, path)
        paths[layer['layerId']] = path
    if packet['schema']=='dcdtuni.render-result-layers/v3':
        paths['volume']=_volume_usd_module().author_common_volume(work,packet,volume_carrier,ROOT,_lut_bytes)
    # The signed receiver holds its context/generation lock through the final
    # swap. Recheck current stage/source AFTER all temporary USD authoring.
    from contextlib import nullcontext
    with commit_guard() if commit_guard is not None else nullcontext():
        _assert_current(stage, packet, expected_source, expected_time_seconds, last_revision)
        return _commit_result_work(stage, work, packet, paths)


@lru_cache(maxsize=1)
def _volume_usd_module():
    if __package__:
        from . import common_volume_usd
        return common_volume_usd
    import importlib.util
    spec=importlib.util.spec_from_file_location('_dcdtuni_common_volume_usd',Path(__file__).with_name('common_volume_usd.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _require_owned_session_outputs(stage):
    """Only installed CAE overrides below ACTUAL root-owned volume paths.

    Operator writes use the session layer. An unknown defined prim, reference,
    variant or another path cannot be silently erased under this permission.
    """
    from pxr import Sdf
    root=stage.GetRootLayer();session=stage.GetSessionLayer();spec=session.GetPrimAtPath(ROOT)
    if not spec:return
    managed=root.GetPrimAtPath(ROOT)
    if not managed or managed.customData.get('dcdtuni',{}).get('schema')!='dcdtuni.render-result-layers/v3':
        raise ValueError('Session result subtree has no managed volume owner')
    volumes=root.GetPrimAtPath(ROOT+'/Volumes')
    prefixes=[str(child.path)for child in volumes.nameChildren]if volumes else []
    if any(not re.fullmatch(ROOT+'/Volumes/v_[a-f0-9]{64}',path)for path in prefixes):
        raise ValueError('Session result owner has unknown volume paths')
    def require(node):
        path=str(node.path)
        if node.specifier!=Sdf.SpecifierOver or node.typeName or (path not in (ROOT,ROOT+'/Volumes')and not any(path==prefix or path.startswith(prefix+'/')for prefix in prefixes)):
            raise ValueError('Session output is outside the managed volume paths')
        for key in ['references','payload','inheritPaths','specializes','variantSetNames','variantSelection']:
            if node.HasInfo(key):raise ValueError('Session result contains unrelated composition edits')
        for child in node.nameChildren:require(child)
    require(spec)


def _owned_result_backup(stage):
    from pxr import Sdf,Usd,UsdGeom
    _require_owned_session_outputs(stage)
    backups=[]
    for layer in [stage.GetRootLayer(),stage.GetSessionLayer()]:
        backup=Usd.Stage.CreateInMemory();UsdGeom.Xform.Define(backup,'/World')
        exists=bool(layer.GetPrimAtPath(ROOT))
        if exists and not Sdf.CopySpec(layer,ROOT,backup.GetRootLayer(),ROOT):
            raise RuntimeError('Could not preserve managed result layer')
        backups.append((layer,backup,exists))
    return backups


def _erase_owned_result_spec(layer):
    from pxr import Sdf
    if not layer.GetPrimAtPath(ROOT):return
    edit=Sdf.BatchNamespaceEdit();edit.Add(ROOT,Sdf.Path.emptyPath)
    if not layer.Apply(edit):raise RuntimeError('Could not remove managed result layer spec')


def _restore_owned_result_specs(backups):
    from pxr import Sdf
    for layer,backup,exists in backups:
        _erase_owned_result_spec(layer)
        if exists and not Sdf.CopySpec(backup.GetRootLayer(),ROOT,layer,ROOT):
            raise RuntimeError('Could not restore managed result layer')


def _commit_result_work(stage, work, packet, paths):
    from pxr import Sdf,UsdGeom
    backups=_owned_result_backup(stage);root_layer=stage.GetRootLayer();previous=stage.GetEditTarget()
    try:
        # One USD notification batch AND one signed receiver final context lock.
        # Both root and session outputs retire before the new unique paths exist.
        with Sdf.ChangeBlock():
            try:
                stage.SetEditTarget(stage.GetSessionLayer())
                _volume_usd_module().retire_common_volumes(stage,ROOT)
                for layer,_,_ in backups:_erase_owned_result_spec(layer)
                stage.SetEditTarget(root_layer)
                if not root_layer.GetPrimAtPath('/World'):Sdf.PrimSpec(root_layer,'World',Sdf.SpecifierDef,'Xform')
                if not Sdf.CopySpec(work.GetRootLayer(),ROOT,root_layer,ROOT):
                    raise RuntimeError('Could not copy validated OpenUSD result layer')
            except BaseException:
                _restore_owned_result_specs(backups)
                raise
        UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.y);UsdGeom.SetStageMetersPerUnit(stage,1)
    finally:stage.SetEditTarget(previous)
    return {'layerPaths':paths,'revision':packet['revision'],'timeSeconds':packet['timeSeconds']}


def clear_result_layers(stage):
    """Clear the managed root PLUS its owned CAE session overrides atomically."""
    from pxr import Sdf
    root_layer=stage.GetRootLayer()
    if not root_layer.GetPrimAtPath(ROOT):
        if stage.GetPrimAtPath(ROOT):raise ValueError('Result subtree is not owned by the active root layer')
        return 0
    prim=stage.GetPrimAtPath(ROOT)
    if prim.GetCustomDataByKey('dcdtuni:schema')not in('dcdtuni.render-result-layers/v1','dcdtuni.render-result-layers/v2','dcdtuni.render-result-layers/v3','dcdtuni.render-result-layers/v4'):
        raise ValueError('Result subtree has no managed renderer schema')
    backups=_owned_result_backup(stage);previous=stage.GetEditTarget()
    try:
        with Sdf.ChangeBlock():
            try:
                stage.SetEditTarget(stage.GetSessionLayer())
                _volume_usd_module().retire_common_volumes(stage,ROOT)
                for layer,_,_ in backups:_erase_owned_result_spec(layer)
            except BaseException:
                _restore_owned_result_specs(backups)
                raise
        if stage.GetPrimAtPath(ROOT):
            with Sdf.ChangeBlock():_restore_owned_result_specs(backups)
            raise RuntimeError('Result subtree includes unowned layer opinions')
    finally:stage.SetEditTarget(previous)
    return 1
