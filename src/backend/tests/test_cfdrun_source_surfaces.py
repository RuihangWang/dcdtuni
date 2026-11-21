"""Strict v2 source-surface provenance is separate from BC/faceZone grouping."""
from copy import deepcopy
import hashlib
import json

import pytest

from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests.test_cfdrun_reader import _minimal_manifest, _write_ascii_stl_rect


def source_manifest():
    raw = _minimal_manifest()
    raw['geometryExport']['stlFiles'] = {
        'stl_000': {'path': 'merged_baffle.stl', 'objectPath': None,
                    'kind': 'grouped_ascii', 'contentHash': 'a' * 64},
    }
    raw['geometryExport']['sourceSurfaces'] = {
        'stl_000:con_f': {
            'stlFileId': 'stl_000', 'solidName': 'con_f',
            'sourceObjectUid': 'm_22', 'sourceFaceUid': 'project/m_22/source-sheet',
            'representation': 'zeroThicknessSheet',
        },
    }
    return raw


def test_source_surface_uid_survives_strict_v2_validation():
    model = CfdrunV2.model_validate(source_manifest())
    saved = model.model_dump(by_alias=True, mode='json', exclude_none=True)
    assert saved['geometryExport']['sourceSurfaces']['stl_000:con_f']['sourceObjectUid'] == 'm_22'
    assert saved['geometryExport']['sourceSurfaces']['stl_000:con_f']['representation'] == 'zeroThicknessSheet'


def test_source_surface_rejects_unbound_file_and_undeclared_pair():
    raw = source_manifest()
    raw['geometryExport']['sourceSurfaces']['stl_000:con_f']['stlFileId'] = 'stl_missing'
    with pytest.raises(ValueError, match='stl_missing|stlFileId'):
        CfdrunV2.model_validate(raw)

    raw = source_manifest()
    raw['geometryExport']['sourceSurfaces']['stl_000:con_f']['pairUid'] = 'invented'
    with pytest.raises(ValueError, match='pairUid|extra'):
        CfdrunV2.model_validate(raw)


def test_source_face_uid_cannot_claim_two_distinct_stl_regions():
    raw = source_manifest()
    copied = deepcopy(raw['geometryExport']['sourceSurfaces']['stl_000:con_f'])
    copied['solidName'] = 'con_b'
    raw['geometryExport']['sourceSurfaces']['stl_000:con_b'] = copied
    with pytest.raises(ValueError, match='sourceFaceUid|duplicate'):
        CfdrunV2.model_validate(raw)


def test_nonhybrid_source_uid_requires_original_stl_hash_before_mesh(tmp_path):
    tri = tmp_path / 'triSurface'
    tri.mkdir()
    stl = tri / 'merged_baffle.stl'
    _write_ascii_stl_rect(stl, 'con_f', 0.4, 0.2)
    raw = source_manifest()
    raw['runId'] = 'ordinary_source_surface'
    raw['geometryExport']['stlFiles']['stl_000']['contentHash'] = hashlib.sha256(stl.read_bytes()).hexdigest()
    manifest = tmp_path / 'ordinary.cfdrun'
    manifest.write_text(json.dumps(raw))
    runner = object.__new__(STLGenRunner)
    runner.cfdrun_path = manifest
    runner.tri_surface_dir = tri
    runner._verify_hybrid_source_stls()
    assert runner._source_stls_verified is True
    stl.write_bytes(stl.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='hash|differs'):
        runner._verify_hybrid_source_stls()
