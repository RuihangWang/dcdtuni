"""A solve-only hybrid handoff reads the uploaded batch its manifest declares.

The mesh job rewrites Mesh/constant/triSurface (merged_baffle.stl is split into
merged_wall_baffle.stl and wallBaffle_floor_*.stl), so a later solve, which
uploads no STL, can only find the exact declared bytes in the original-input
archive that mesh job kept.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def api(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'server.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location('hybrid_solve_batch_server', path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _archive(case, input_id, files):
    tri = case / 'log' / 'air-inputs' / input_id / 'Mesh' / 'constant' / 'triSurface'
    tri.mkdir(parents=True)
    for name, data in files.items():
        (tri / name).write_bytes(data)
    return tri


def _manifest(files):
    return {'geometryExport': {'stlFiles': {
        f'stl_{i:03d}': {'path': name, 'contentHash': hashlib.sha256(data).hexdigest()}
        for i, (name, data) in enumerate(sorted(files.items()))}}}


def test_finds_the_archived_batch_whose_bytes_match_the_manifest(api, tmp_path):
    old = {'merged_baffle.stl': b'solid a\nendsolid a\n'}
    new = {'merged_baffle.stl': b'solid b\nendsolid b\n', 'room_wall.stl': b'solid w\nendsolid w\n'}
    _archive(tmp_path, '00000000-0000-0000-0000-000000000001', old)
    want = _archive(tmp_path, '00000000-0000-0000-0000-000000000002', new)
    live = tmp_path / 'Mesh' / 'constant' / 'triSurface'
    live.mkdir(parents=True)
    (live / 'merged_wall_baffle.stl').write_bytes(b'derived by the runner')
    assert api._archived_stl_batch_dir(tmp_path, _manifest(new)) == want


def test_no_archive_with_the_exact_set_gives_none(api, tmp_path):
    files = {'merged_baffle.stl': b'solid b\nendsolid b\n'}
    _archive(tmp_path, '00000000-0000-0000-0000-000000000003',
             {**files, 'extra.stl': b'solid x\nendsolid x\n'})
    assert api._archived_stl_batch_dir(tmp_path, _manifest(files)) is None
    assert api._archived_stl_batch_dir(tmp_path, {'geometryExport': {}}) is None
