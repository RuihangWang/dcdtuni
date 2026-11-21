"""CPU OpenUSD authoring checks for derived, renderer-neutral result packets."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

pxr = pytest.importorskip('pxr')
from pxr import Usd, UsdGeom, UsdShade  # noqa: E402


MODULE_PATH = (Path(__file__).resolve().parents[2]
               / 'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/result_layers_usd.py')
spec = importlib.util.spec_from_file_location('result_layers_usd_tested', MODULE_PATH)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def packet():
    source = {
        'projectUid': 'project-a', 'projectContentSha256': 'a' * 64,
        'cfdrunSha256': 'b' * 64, 'meshReceiptSha256': 'c' * 64,
        'meshQuality': 'accepted', 'run': {'uid': 'run-7', 'receiptSha256': 'd' * 64},
    }
    positions = [0, 0, 0, 1, 0, 0, 0, 1, 0]
    provenance = ['project-a/m_22/source-sheet', 'project-a/m_23/source-sheet',
                  'project-a/m_24/source-sheet']
    field = {'name': 'T', 'unit': 'K'}
    lut = {'palette': 'coolwarm', 'range': [290, 310], 'opacity': .8}
    return {
        'schema': 'dcdtuni.render-result-layers/v1', 'revision': 4,
        'source': source,
        'coordinateSystem': {'upAxis': 'Y', 'metersPerUnit': 1, 'handedness': 'right'},
        'timeSeconds': 20,
        'layers': [
            {'kind': 'meshEdges', 'layerId': 'meshEdges/merged_wall_baffle',
             'visible': True, 'meshPatchName': 'merged_wall_baffle',
             'sourceFaceUids': provenance, 'positions': positions,
             'indices': [0, 1, 1, 2, 2, 0], 'opacity': .5},
            {'kind': 'surfaceField', 'layerId': 'surfaceField/T/merged_wall_baffle',
             'visible': True, 'meshPatchName': 'merged_wall_baffle',
             'sourceFaceUids': provenance, 'positions': positions,
             'indices': [0, 1, 2], 'values': [290, 300, 310],
             'field': field, 'lut': lut},
            {'kind': 'streamline', 'layerId': 'streamline/seed-1/U',
             'visible': False, 'sourceSeedUid': 'seed-1',
             'lines': [
                 {'positions': [0, 0, 0, 1, 0, 0, 2, 0, 0], 'values': [0, .5, 1]},
                 {'positions': [0, 1, 0, 1, 1, 0], 'values': [.25, .75]},
             ],
             'field': {'name': 'U', 'unit': 'm/s'},
             'lut': {'palette': 'viridis', 'range': [0, 1], 'opacity': 1},
             'tubeRadiusMeters': .01},
        ],
    }


def _author(stage, body, **kwargs):
    return adapter.author_result_layers(
        stage, body, expected_source=body['source'],
        expected_time_seconds=body['timeSeconds'], **kwargs,
    )


def _by_kind(stage):
    root = stage.GetPrimAtPath('/World/RenderResultLayers')
    return {child.GetCustomDataByKey('dcdtuni:kind'): child
            for child in root.GetChild('Layers').GetChildren()}


def test_cpu_usd_authors_three_result_kinds_with_raw_values_lut_and_provenance(tmp_path):
    body = packet()
    stage = Usd.Stage.CreateInMemory()
    receipt = _author(stage, body)
    assert receipt['revision'] == 4
    assert set(receipt['layerPaths']) == {layer['layerId'] for layer in body['layers']}
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1
    root = stage.GetPrimAtPath('/World/RenderResultLayers')
    assert root.GetCustomDataByKey('dcdtuni:projectUid') == 'project-a'
    assert root.GetCustomDataByKey('dcdtuni:meshReceiptSha256') == 'c' * 64
    assert root.GetCustomDataByKey('dcdtuni:runReceiptSha256') == 'd' * 64
    assert root.GetCustomDataByKey('dcdtuni:timeSeconds') == 20
    assert root.GetCustomDataByKey('dcdtuni:revision') == 4

    prims = _by_kind(stage)
    edges = UsdGeom.BasisCurves(prims['meshEdges'])
    assert list(edges.GetCurveVertexCountsAttr().Get()) == [2, 2, 2]
    assert len(edges.GetPointsAttr().Get()) == 6
    assert prims['meshEdges'].GetCustomDataByKey('dcdtuni:meshPatchName') == 'merged_wall_baffle'
    assert list(prims['meshEdges'].GetCustomDataByKey('dcdtuni:sourceFaceUids')) == body['layers'][0]['sourceFaceUids']

    surface = UsdGeom.Mesh(prims['surfaceField'])
    assert list(surface.GetFaceVertexCountsAttr().Get()) == [3]
    assert list(surface.GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]
    scalar = UsdGeom.PrimvarsAPI(surface).GetPrimvar('dcdtuniRawScalar')
    assert scalar.GetInterpolation() == 'vertex'
    assert list(scalar.Get()) == [290, 300, 310]
    colors = UsdGeom.PrimvarsAPI(surface).GetPrimvar('displayColor')
    assert colors.GetInterpolation() == 'vertex'
    assert len(colors.Get()) == 3
    assert tuple(round(x * 255) for x in colors.Get()[0]) == (59, 76, 192)
    assert tuple(round(x * 255) for x in colors.Get()[2]) == (180, 4, 38)
    assert UsdShade.MaterialBindingAPI(surface).ComputeBoundMaterial()[0]
    assert prims['surfaceField'].GetCustomDataByKey('dcdtuni:fieldUnit') == 'K'

    stream = UsdGeom.BasisCurves(prims['streamline'])
    assert list(stream.GetCurveVertexCountsAttr().Get()) == [3, 2]
    assert len(stream.GetPointsAttr().Get()) == 5
    assert list(UsdGeom.PrimvarsAPI(stream).GetPrimvar('dcdtuniRawScalar').Get()) == [0, .5, 1, .25, .75]
    assert stream.GetWidthsAttr().Get()[0] == pytest.approx(.02)
    assert prims['streamline'].GetCustomDataByKey('dcdtuni:sourceSeedUid') == 'seed-1'
    assert UsdGeom.Imageable(prims['streamline']).ComputeVisibility() == UsdGeom.Tokens.invisible

    output = tmp_path / 'result.usda'
    assert stage.GetRootLayer().Export(str(output))
    reopened = Usd.Stage.Open(str(output))
    assert set(_by_kind(reopened)) == {'meshEdges', 'surfaceField', 'streamline'}
    assert list(_by_kind(reopened)['surfaceField'].GetCustomDataByKey('dcdtuni:sourceFaceUids')) == body['layers'][1]['sourceFaceUids']
    assert list(UsdGeom.Mesh(_by_kind(reopened)['surfaceField']).GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]


def test_cpu_usd_authors_internal_slice_without_inventing_a_boundary_patch():
    body = packet()
    body['layers'].append({
        'kind': 'slice', 'layerId': 'slice/cut-1', 'visible': True,
        'sourceSliceUid': 'cut-1', 'originMeters': [0, .5, 0], 'normal': [0, 1, 0],
        'positions': [0, .5, 0, 1, .5, 0, 0, .5, 1], 'indices': [0, 1, 2],
        'values': [290, 295, 300], 'field': {'name': 'T', 'unit': 'K'},
        'lut': {'palette': 'coolwarm', 'range': [290, 310], 'opacity': 1},
    })
    stage = Usd.Stage.CreateInMemory()
    receipt = _author(stage, body)
    prim = stage.GetPrimAtPath(receipt['layerPaths']['slice/cut-1'])
    assert prim.GetCustomDataByKey('dcdtuni:sourceSliceUid') == 'cut-1'
    assert not prim.GetCustomDataByKey('dcdtuni:meshPatchName')
    assert list(UsdGeom.PrimvarsAPI(prim).GetPrimvar('dcdtuniRawScalar').Get()) == [290, 295, 300]
    assert list(UsdGeom.Mesh(prim).GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]


def test_result_transfer_requires_a_separate_current_selection():
    body = packet()
    transfer = {'packet': body, 'expectedSource': copy.deepcopy(body['source']),
                'expectedTimeSeconds': body['timeSeconds']}
    adapter.validate_result_transfer(transfer)
    wrong = copy.deepcopy(transfer)
    wrong['expectedSource']['meshReceiptSha256'] = 'e' * 64
    with pytest.raises(ValueError, match='source'):
        adapter.validate_result_transfer(wrong)
    wrong = copy.deepcopy(transfer)
    wrong['expectedTimeSeconds'] = 21
    with pytest.raises(ValueError, match='time'):
        adapter.validate_result_transfer(wrong)


def test_diagnostic_mesh_edges_allowed_but_any_scalar_layer_rejected_before_usd_mutation():
    body = packet()
    body['source']['meshQuality'] = 'diagnostic'
    body['source']['run'] = None
    body['timeSeconds'] = None
    body['layers'] = body['layers'][:1]
    stage = Usd.Stage.CreateInMemory()
    _author(stage, body)
    assert set(_by_kind(stage)) == {'meshEdges'}
    old_layer = stage.GetRootLayer().ExportToString()
    body['layers'].append(packet()['layers'][1])
    with pytest.raises(ValueError, match='accepted mesh quality|run.*time|scalar'):
        _author(stage, body)
    assert stage.GetRootLayer().ExportToString() == old_layer


@pytest.mark.parametrize('alter', [
    lambda p: p['source'].pop('meshReceiptSha256'),
    lambda p: p['layers'][1]['positions'].__setitem__(0, float('nan')),
    lambda p: p['layers'][1]['indices'].__setitem__(2, 9),
    lambda p: p['layers'][2]['lines'][1]['values'].pop(),
    lambda p: p['layers'][1]['sourceFaceUids'].reverse(),
    lambda p: p['layers'][1].update(modelUid='not-editable'),
    lambda p: p['layers'][1]['lut']['range'].reverse(),
    lambda p: p['coordinateSystem'].__setitem__('metersPerUnit', True),
])
def test_invalid_identity_geometry_or_scalar_data_never_replaces_existing_usd(alter):
    stage = Usd.Stage.CreateInMemory()
    _author(stage, packet())
    before = stage.GetRootLayer().ExportToString()
    corrupt = packet()
    alter(corrupt)
    with pytest.raises(ValueError):
        _author(stage, corrupt)
    assert stage.GetRootLayer().ExportToString() == before


def test_stale_source_time_and_revision_rejected_without_changing_existing_layer():
    stage = Usd.Stage.CreateInMemory()
    body = packet()
    _author(stage, body)
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match='meshReceiptSha256'):
        adapter.author_result_layers(stage, body, expected_source={**body['source'], 'meshReceiptSha256': 'e' * 64}, expected_time_seconds=20)
    with pytest.raises(ValueError, match='time'):
        adapter.author_result_layers(stage, body, expected_source=body['source'], expected_time_seconds=40)
    with pytest.raises(ValueError, match='revision'):
        _author(stage, body)
    with pytest.raises(ValueError, match='revision'):
        _author(stage, copy.deepcopy(body), last_revision=5)
    assert stage.GetRootLayer().ExportToString() == before


def test_new_revision_replaces_only_result_subtree_and_keeps_editor_scene():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, '/World/RenderScene')
    first = packet()
    _author(stage, first)
    newer = packet()
    newer['revision'] = 5
    newer['timeSeconds'] = 40
    newer['layers'] = newer['layers'][:1]
    _author(stage, newer, last_revision=4)
    assert stage.GetPrimAtPath('/World/RenderScene')
    assert set(_by_kind(stage)) == {'meshEdges'}
    root = stage.GetPrimAtPath('/World/RenderResultLayers')
    assert root.GetCustomDataByKey('dcdtuni:revision') == 5
    assert root.GetCustomDataByKey('dcdtuni:timeSeconds') == 40


def test_baked_palette_endpoints_match_threejs_256_entry_presets():
    assert adapter._lut_bytes('coolwarm')[0] == (59, 76, 192)
    assert adapter._lut_bytes('coolwarm')[-1] == (180, 4, 38)
    assert adapter._lut_bytes('jet')[0] == (0, 0, 128)
    assert adapter._lut_bytes('viridis')[0] == (68, 1, 84)
    assert adapter._lut_bytes('viridis')[-1] == (253, 231, 37)
