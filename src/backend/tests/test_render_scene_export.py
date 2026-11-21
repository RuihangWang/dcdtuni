"""Pure-Python render artifact persistence; no server, solver, Kit or GPU."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / 'server/render_scene_files.py'
spec = importlib.util.spec_from_file_location('render_scene_files_tested', MODULE_PATH)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


def scene():
    return {
        'schema': 'dcdtuni.render-scene/v1',
        'project': {'uid': 'project-1', 'name': '../../unsafe project/名'},
        'coordinateSystem': {'upAxis': 'Y', 'metersPerUnit': 1, 'handedness': 'right'},
        'primitives': [{
            'id': 'rack/triangle', 'modelUid': 'rack-1', 'name': 'rack', 'kind': 'triangles',
            'positions': [0, 0, 0, 1, 0, 0, 0, 1, 0], 'indices': [0, 1, 2],
            'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3, 1, 2, 1],
            'visible': True,
            'material': {'color': [1, 0, 0], 'emissive': [0, 0, 0], 'opacity': 1,
                         'roughness': .4, 'metalness': .7, 'doubleSided': True, 'unlit': False},
        }],
        'textures': {}, 'view': {'selectedUids': ['rack-1'], 'timeSeconds': 12.5}, 'warnings': [],
    }


def test_persists_complete_derived_json_and_returns_verified_receipt(tmp_path):
    body = {'scene': scene(), 'case_dir': str(tmp_path)}
    before = copy.deepcopy(body)
    receipt = writer.export_render_scene(body, captured_case_dir=tmp_path)
    path = Path(receipt['path'])
    assert path.parent == tmp_path / 'Render/scenes'
    assert path.name.endswith('.renderscene.json') and '..' not in path.name
    assert json.loads(path.read_text()) == body['scene']
    assert receipt == {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'project_uid': 'project-1', 'time_seconds': 12.5, 'primitives': 1, 'textures': 0}
    assert body == before
    assert list(path.parent.iterdir()) == [path]


def test_export_never_overwrites_a_previous_artifact(tmp_path):
    first = writer.export_render_scene({'scene': scene(), 'case_dir': str(tmp_path)}, captured_case_dir=tmp_path)
    old_bytes = Path(first['path']).read_bytes()
    next_scene = scene()
    next_scene['view']['timeSeconds'] = None
    second = writer.export_render_scene({'scene': next_scene, 'case_dir': str(tmp_path)}, captured_case_dir=tmp_path)
    assert first['path'] != second['path']
    assert Path(first['path']).read_bytes() == old_bytes
    assert json.loads(Path(second['path']).read_text())['view']['timeSeconds'] is None


def test_a_filename_collision_preserves_existing_file(tmp_path, monkeypatch):
    class FixedUuid:
        hex = 'a' * 32
    monkeypatch.setattr(writer.uuid, 'uuid4', lambda: FixedUuid())
    body = {'scene': scene(), 'case_dir': str(tmp_path)}
    receipt = writer.export_render_scene(body, captured_case_dir=tmp_path)
    path = Path(receipt['path'])
    original = path.read_bytes()
    body['scene']['warnings'] = ['new data must not replace prior file']
    with pytest.raises(FileExistsError):
        writer.export_render_scene(body, captured_case_dir=tmp_path)
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_case_switch_mismatch_rejected_before_any_output(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    with pytest.raises(ValueError, match='case_dir'):
        writer.export_render_scene({'scene': scene(), 'case_dir': str(other)}, captured_case_dir=tmp_path)
    assert not (tmp_path / 'Render').exists()
    assert not (other / 'Render').exists()


@pytest.mark.parametrize('extra', ['path', 'output', 'filename'])
def test_rejects_caller_output_paths_and_other_unknown_body_fields(tmp_path, extra):
    with pytest.raises(ValueError):
        writer.export_render_scene({'scene': scene(), 'case_dir': str(tmp_path), extra: '../escape'}, captured_case_dir=tmp_path)
    assert not (tmp_path / 'Render').exists()


@pytest.mark.parametrize('alter', [
    lambda s: s['primitives'][0]['indices'].__setitem__(0, 99),
    lambda s: s['view'].update(camera={'position': [0, 0, 1], 'target': [0, 0, 0], 'up': [0, 1, 0], 'fov': 50, 'aspect': 1, 'near': 20, 'far': 10}),
    lambda s: s['view'].update(camera={'position': [0, 0, 0], 'target': [0, 0, 0], 'up': [0, 1, 0], 'fov': 50, 'aspect': 1, 'near': .1, 'far': 10}),
    lambda s: s['primitives'][0]['positions'].__setitem__(0, float('nan')),
    lambda s: s['textures'].update(texture='data:image/png;base64,bm90IGEgcG5n'),
])
def test_reuses_geometry_texture_and_full_view_semantic_validation(tmp_path, alter):
    packet = scene()
    alter(packet)
    with pytest.raises(ValueError):
        writer.export_render_scene({'scene': packet, 'case_dir': str(tmp_path)}, captured_case_dir=tmp_path)
    assert not (tmp_path / 'Render').exists()


@pytest.mark.parametrize('component', ['Render', 'scenes'])
def test_rejects_symlink_escape_at_each_output_directory(tmp_path, component):
    case = tmp_path / 'case'
    case.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    if component == 'scenes':
        (case / 'Render').mkdir()
    target = case / 'Render' if component == 'Render' else case / 'Render/scenes'
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='directory|symlink'):
        writer.export_render_scene({'scene': scene(), 'case_dir': str(case)}, captured_case_dir=case)
    assert list(outside.iterdir()) == []


def test_request_byte_cap_is_checked_before_validator_or_directory_creation(tmp_path, monkeypatch):
    assert writer.MAX_REQUEST_BYTES == 64 * 1024 * 1024
    monkeypatch.setattr(writer, 'MAX_REQUEST_BYTES', 200)
    with pytest.raises(ValueError, match='64 MiB|size limit'):
        writer.export_render_scene({'scene': scene(), 'case_dir': str(tmp_path)}, captured_case_dir=tmp_path)
    assert not (tmp_path / 'Render').exists()


def test_atomic_publish_failure_leaves_no_partial_json_or_temp_file(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError('publication interrupted')
    monkeypatch.setattr(writer.os, 'link', fail)
    with pytest.raises(OSError, match='interrupted'):
        writer.export_render_scene({'scene': scene(), 'case_dir': str(tmp_path)}, captured_case_dir=tmp_path)
    assert list((tmp_path / 'Render/scenes').iterdir()) == []


def test_import_and_validation_do_not_load_torch_or_usd(tmp_path):
    source = """
import importlib.util,json,pathlib,sys
spec=importlib.util.spec_from_file_location('pure_writer',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.export_render_scene(json.loads(sys.argv[3]),captured_case_dir=pathlib.Path(sys.argv[2]))
assert not any(x=='torch' or x.startswith('torch.') or x=='pxr' or x.startswith('pxr.') or x=='omni' or x.startswith('omni.') for x in sys.modules)
"""
    result = subprocess.run([sys.executable, '-c', source, str(MODULE_PATH), str(tmp_path), json.dumps({'scene': scene(), 'case_dir': str(tmp_path)})], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
