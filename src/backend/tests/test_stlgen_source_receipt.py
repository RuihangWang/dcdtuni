"""A non-hybrid source UID map must remain bound to its generated air mesh."""
import json
import hashlib
import shutil

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests.test_cfdrun_source_surfaces import source_manifest
from backend.tests.test_stlgen_hybrid_gate import _stub_runner


def _runner(tmp_path):
    runner = object.__new__(STLGenRunner)
    runner.case_dir = tmp_path
    runner.mesh_dir = tmp_path / 'Mesh'
    runner.tri_surface_dir = runner.mesh_dir / 'constant/triSurface'
    runner.tri_surface_dir.mkdir(parents=True)
    poly = runner.mesh_dir / 'constant/polyMesh'
    poly.mkdir(parents=True)
    for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
        (poly / name).write_bytes(f'{name}: original mesh\n'.encode())
    (poly / 'owner').write_text('FoamFile { note "nCells:1"; object owner; }\n')
    model = tmp_path / 'model'
    model.mkdir()
    runner.cfdrun_path = model / 'ordinary.cfdrun'
    raw = source_manifest()
    raw['runId'] = 'ordinary_source_surface'
    raw['sourceProject']['contentHash'] = 'a' * 64
    runner.cfdrun_path.write_text(json.dumps(raw))
    runner._source_stls_verified = True
    runner._verified_source_cfdrun_sha256 = hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    return runner, raw, poly


def test_nonhybrid_mesh_receipt_rejects_uid_or_mesh_changes(tmp_path):
    runner, raw, poly = _runner(tmp_path)
    runner._record_source_surface_mesh_source()
    receipt = tmp_path / 'source-surface-mesh-source.json'
    assert receipt.is_file()
    runner._verify_source_surface_mesh_source()

    changed = json.loads(json.dumps(raw))
    changed['geometryExport']['sourceSurfaces']['stl_000:con_f']['sourceObjectUid'] = 'm_23'
    runner.cfdrun_path.write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match='source.*identity|receipt|does not match'):
        runner._verify_source_surface_mesh_source()

    changed = json.loads(json.dumps(raw))
    changed['geometryExport']['stlFiles']['stl_000']['path'] = 'different.stl'
    runner.cfdrun_path.write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match='source.*identity|receipt|does not match'):
        runner._verify_source_surface_mesh_source()

    runner.cfdrun_path.write_text(json.dumps(raw))
    (poly / 'faces').write_bytes(b'changed mesh')
    with pytest.raises(RuntimeError, match='source.*identity|receipt|does not match'):
        runner._verify_source_surface_mesh_source()


def test_nonhybrid_source_solve_requires_prior_receipt(tmp_path):
    runner, _, _ = _runner(tmp_path)
    with pytest.raises(RuntimeError, match='source.*receipt|source-surface-mesh-source'):
        runner._verify_source_surface_mesh_source()


def test_legacy_without_source_surfaces_needs_no_new_receipt(tmp_path):
    runner, raw, _ = _runner(tmp_path)
    raw['geometryExport']['sourceSurfaces'] = {}
    runner.cfdrun_path.write_text(json.dumps(raw))
    # This intentionally changes the input between independent runs. The
    # production CLI constructs a fresh runner for the new manifest.
    runner._verified_source_cfdrun_sha256 = None
    runner._verify_source_surface_mesh_source()
    runner._record_source_surface_mesh_source()
    assert not (tmp_path / 'source-surface-mesh-source.json').exists()


def test_full_and_solve_only_paths_put_receipt_next_to_mesh_use(monkeypatch):
    runner, events = _stub_runner(monkeypatch)
    runner._data = {'runId': 'ordinary', 'meshConfig': {'meshEngine': 'snappy'}}
    runner.run_simulation()
    assert events.index('_record_source_surface_mesh_source') < events.index('_run_solve_from_mesh')
    events.clear()
    runner.run_solve_only()
    assert events.index('_setup_complete_case_in_mesh') < events.index('_verify_source_surface_mesh_source')
    assert events.index('_verify_source_surface_mesh_source') < events.index('_run_solve_from_mesh')


def test_nonhybrid_partitioned_mesh_receipt_binds_every_rank(tmp_path):
    runner, raw, serial_mesh = _runner(tmp_path)
    shutil.rmtree(serial_mesh)
    for rank in (0, 1):
        poly = runner.mesh_dir / f'processor{rank}/constant/polyMesh'
        poly.mkdir(parents=True)
        for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
            (poly / name).write_bytes(f'rank{rank}:{name}:original\n'.encode())

    runner._record_source_surface_mesh_source()
    assert (tmp_path / 'source-surface-mesh-source.json').is_file()
    runner._verify_source_surface_mesh_source()
    (runner.mesh_dir / 'processor1/constant/polyMesh/faces').write_bytes(b'rank1:changed')
    with pytest.raises(RuntimeError, match='source.*identity|receipt|does not match'):
        runner._verify_source_surface_mesh_source()
