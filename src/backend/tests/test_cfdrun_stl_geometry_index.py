"""Actual grouped STL geometry must determine the area of air heat patches."""

import copy
import hashlib
import json

import pytest

from backend.aircooling.runner.cfdrun_reader import resolve_run
from backend.tests.test_cfdrun_reader import _minimal_manifest, _write_ascii_stl_rect


def manifest_with_hot_patch():
    raw = _minimal_manifest()
    raw['geometryExport']['solids'] = {
        'sol_hot_front': {
            'solidName': 'hot_front', 'patchName': 'hot_front',
            'stlFileId': None, 'objectPath': 'constructions.racks.r1.servers.s1',
            'surfaceUid': None, 'role': 'nonfan_fixed_temp', 'areaM2': None,
        },
    }
    raw['boundaryBindings'] = {
        'bb_hot_front': {
            'patchName': 'hot_front', 'solidName': 'hot_front',
            'objectPath': 'constructions.racks.r1.servers.s1',
            'surfaceUid': None, 'role': 'nonfan_fixed_temp',
            'bcType': 'nonfan_fixed_temp', 'parameters': {'temperatureC': 35},
        },
    }
    return raw


def test_index_fills_real_area_and_file_identity_then_reader_reopens(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import index_cfdrun_stls

    case = tmp_path / 'case'
    tri = case / 'Mesh/constant/triSurface'
    tri.mkdir(parents=True)
    stl = tri / 'group.stl'
    _write_ascii_stl_rect(stl, 'hot_front', 0.4, 0.2)
    raw = manifest_with_hot_patch()
    before = copy.deepcopy(raw)

    enriched = index_cfdrun_stls(raw, tri)
    assert raw == before
    solid = enriched['geometryExport']['solids']['sol_hot_front']
    assert solid['areaM2'] == pytest.approx(0.08)
    file_id = solid['stlFileId']
    assert enriched['geometryExport']['stlFiles'][file_id]['path'] == 'group.stl'
    assert enriched['geometryExport']['stlFiles'][file_id]['contentHash'] == hashlib.sha256(stl.read_bytes()).hexdigest()
    assert index_cfdrun_stls(enriched, tri) == enriched

    model = case / 'model'
    model.mkdir()
    (model / 'group.cfdrun').write_text(json.dumps(enriched))
    resolved = resolve_run(case)
    assert resolved.geometry_solids['sol_hot_front']['areaM2'] == pytest.approx(0.08)
    assert resolved.bridge_registry.get('hot_front').bc_type == 'nonfan_fixed_temp'


def test_index_rejects_duplicate_or_missing_or_zero_area_solids(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import index_cfdrun_stls

    tri = tmp_path / 'triSurface'
    tri.mkdir()
    _write_ascii_stl_rect(tri / 'a.stl', 'hot_front', 0.4, 0.2)
    _write_ascii_stl_rect(tri / 'b.stl', 'hot_front', 0.4, 0.2)
    with pytest.raises(ValueError, match='duplicate.*hot_front'):
        index_cfdrun_stls(manifest_with_hot_patch(), tri)

    (tri / 'b.stl').unlink()
    missing = manifest_with_hot_patch()
    missing['geometryExport']['solids']['sol_hot_front']['solidName'] = 'absent'
    with pytest.raises(ValueError, match='absent|missing'):
        index_cfdrun_stls(missing, tri)

    _write_ascii_stl_rect(tri / 'a.stl', 'hot_front', 0.0, 0.2)
    with pytest.raises(ValueError, match='area|degenerate'):
        index_cfdrun_stls(manifest_with_hot_patch(), tri)


def test_index_refuses_to_replace_a_conflicting_declared_area(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import index_cfdrun_stls

    tri = tmp_path / 'triSurface'
    tri.mkdir()
    _write_ascii_stl_rect(tri / 'group.stl', 'hot_front', 0.4, 0.2)
    raw = manifest_with_hot_patch()
    raw['geometryExport']['solids']['sol_hot_front']['areaM2'] = 0.16
    with pytest.raises(ValueError, match='areaM2|area'):
        index_cfdrun_stls(raw, tri)


def test_targeted_index_allows_unmaterialized_backend_aggregate_but_not_target_gap(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import index_cfdrun_stls

    tri = tmp_path / 'triSurface'
    tri.mkdir()
    _write_ascii_stl_rect(tri / 'group.stl', 'hot_front', 0.4, 0.2)
    raw = manifest_with_hot_patch()
    raw['geometryExport']['solids']['sol_generated'] = {
        'solidName': 'generated_wall_baffle', 'patchName': 'generated_wall_baffle',
        'stlFileId': None, 'objectPath': 'constructions.baffles.b1',
        'surfaceUid': None, 'role': 'wall', 'areaM2': None,
    }
    enriched = index_cfdrun_stls(raw, tri, target_solid_names={'hot_front'})
    assert enriched['geometryExport']['solids']['sol_hot_front']['areaM2'] == pytest.approx(.08)
    assert enriched['geometryExport']['solids']['sol_generated']['areaM2'] is None
    with pytest.raises(ValueError, match='generated_wall_baffle|missing'):
        index_cfdrun_stls(raw, tri)
    with pytest.raises(ValueError, match='absent|target'):
        index_cfdrun_stls(raw, tri, target_solid_names={'absent'})


def test_export_time_stl_hashes_reject_changed_geometry_before_remeshing(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import verify_exported_stl_hashes

    tri = tmp_path / 'triSurface'
    tri.mkdir()
    stl = tri / 'group.stl'
    _write_ascii_stl_rect(stl, 'hot_front', .4, .2)
    raw = manifest_with_hot_patch()
    raw['geometryExport']['stlFiles'] = {
        'stl_000': {'path': 'group.stl', 'objectPath': None,
                    'kind': 'grouped_ascii', 'contentHash': hashlib.sha256(stl.read_bytes()).hexdigest()},
    }
    assert verify_exported_stl_hashes(raw, tri)['file_count'] == 1
    stl.write_text(stl.read_text().replace('vertex 0.0 0.0 0.0', 'vertex 1.0 0.0 0.0'))
    with pytest.raises(ValueError, match='hash|digest|content'):
        verify_exported_stl_hashes(raw, tri)


def test_source_surface_identity_must_name_a_solid_in_its_hashed_stl(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import verify_exported_stl_hashes

    tri = tmp_path / 'triSurface'
    tri.mkdir()
    stl = tri / 'group.stl'
    _write_ascii_stl_rect(stl, 'hot_front', .4, .2)
    raw = manifest_with_hot_patch()
    raw['geometryExport']['stlFiles'] = {
        'stl_000': {'path': 'group.stl', 'objectPath': None,
                    'kind': 'grouped_ascii', 'contentHash': hashlib.sha256(stl.read_bytes()).hexdigest()},
    }
    raw['geometryExport']['sourceSurfaces'] = {
        'stl_000:wrong_solid': {'stlFileId': 'stl_000', 'solidName': 'wrong_solid',
                                'sourceObjectUid': 'rack/server',
                                'sourceFaceUid': 'project/rack/server/front',
                                'representation': 'surfaceFace'},
    }
    with pytest.raises(ValueError, match='wrong_solid|sourceSurfaces|STL solid'):
        verify_exported_stl_hashes(raw, tri)


def test_source_surface_identity_rejects_empty_or_degenerate_solid(tmp_path):
    from backend.aircooling.runner.stl_geometry_index import verify_exported_stl_hashes

    tri = tmp_path / 'triSurface'
    tri.mkdir()
    stl = tri / 'group.stl'
    stl.write_text('solid hot_front\nendsolid hot_front\n')
    raw = manifest_with_hot_patch()
    raw['geometryExport']['stlFiles'] = {
        'stl_000': {'path': 'group.stl', 'objectPath': None,
                    'kind': 'grouped_ascii', 'contentHash': hashlib.sha256(stl.read_bytes()).hexdigest()},
    }
    raw['geometryExport']['sourceSurfaces'] = {
        'stl_000:hot_front': {'stlFileId': 'stl_000', 'solidName': 'hot_front',
                              'sourceObjectUid': 'rack/server',
                              'sourceFaceUid': 'project/rack/server/front',
                              'representation': 'surfaceFace'},
    }
    with pytest.raises(ValueError, match='degenerate|area|triangles'):
        verify_exported_stl_hashes(raw, tri)
