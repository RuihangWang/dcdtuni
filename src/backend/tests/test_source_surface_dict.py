"""The dcCart dictionary must preserve exact STL-region source identities."""
import pytest

from backend.aircooling.runner.source_surface_dict import render_source_surfaces_dict
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def geometry():
    return {
        'coordinateSystem': 'Y-up', 'unit': 'm', 'solids': {},
        'stlFiles': {'stl_001': {'path': 'merged_baffle.stl', 'objectPath': None,
                                 'kind': 'grouped_ascii', 'contentHash': 'a' * 64}},
        'sourceSurfaces': {'stl_001:con_f': {'stlFileId': 'stl_001', 'solidName': 'con_f',
                                            'sourceObjectUid': 'm_22',
                                            'sourceFaceUid': 'proj_Shinaide/m_22/source-sheet',
                                            'representation': 'zeroThicknessSheet'}},
    }


def test_renders_one_exact_file_region_to_source_uid_mapping():
    output = render_source_surfaces_dict(geometry())
    assert 'dcdtuniSourceSurfaces' in output
    assert 'merged_baffle.stl' in output and 'con_f' in output
    assert 'sourceObjectUid "m_22";' in output
    assert 'sourceFaceUid "proj_Shinaide/m_22/source-sheet";' in output
    assert 'representation zeroThicknessSheet;' in output


def test_old_manifest_has_no_source_dictionary_and_unsafe_uid_is_rejected():
    data = geometry()
    data['sourceSurfaces'] = {}
    assert render_source_surfaces_dict(data) == ''
    data = geometry()
    data['sourceSurfaces']['stl_001:con_f']['sourceObjectUid'] = 'm_22"; inject true;'
    with pytest.raises(ValueError, match='sourceObjectUid|unsafe'):
        render_source_surfaces_dict(data)


def test_runner_adds_source_dictionary_only_for_dccart(tmp_path):
    import json

    runner = object.__new__(STLGenRunner)
    runner.cfdrun_path = tmp_path / 'source.cfdrun'
    runner.cfdrun_path.write_text(json.dumps({'geometryExport': geometry()}))
    with pytest.raises(RuntimeError, match='verified export-time STL'):
        runner._source_surface_dict_block({'meshEngine': 'dcCart'})
    runner._source_stls_verified = True
    assert 'sourceObjectUid "m_22"' in runner._source_surface_dict_block({'meshEngine': 'dcCart'})
    assert runner._source_surface_dict_block({'meshEngine': 'snappy'}) == ''


def test_mesh_stage_rebinds_source_region_to_its_unique_actual_stl_file():
    output = render_source_surfaces_dict(
        geometry(), {'merged_wall_baffle.stl': ['con_f']})
    assert 'merged_wall_baffle.stl' in output
    assert 'merged_baffle.stl' not in output
    with pytest.raises(ValueError, match='con_f|duplicate|unique'):
        render_source_surfaces_dict(geometry(), {
            'merged_wall_baffle.stl': ['con_f'], 'another.stl': ['con_f']})
    with pytest.raises(ValueError, match='con_f|missing'):
        render_source_surfaces_dict(geometry(), {'merged_wall_baffle.stl': ['other']})


def test_rebucket_cannot_collapse_two_source_bindings_to_one_mesh_region():
    data = geometry()
    data['stlFiles']['stl_002'] = {**data['stlFiles']['stl_001'], 'path': 'other.stl'}
    data['sourceSurfaces']['stl_002:con_f'] = {
        **data['sourceSurfaces']['stl_001:con_f'],
        'stlFileId': 'stl_002', 'sourceObjectUid': 'm_23',
        'sourceFaceUid': 'proj_Shinaide/m_23/source-sheet',
    }
    with pytest.raises(ValueError, match='duplicate mesh-stage source region'):
        render_source_surfaces_dict(data, {'merged_wall_baffle.stl': ['con_f']})
