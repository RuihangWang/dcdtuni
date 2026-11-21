"""A finished DAE run survives a server restart as a read-only owned record.

The record is rebuilt from the files the manager persisted (input.liquidrun,
input.json, checkpoint.json, result.json, executed.liquidrun and
executed-inputs.json), with every identity link checked again, so the hybrid
air handoff keeps its full audit: project content hash, accepted-input hash
and completed status. Nothing is re-solved, so a run made on a GPU restores on
a host without one.
"""
from __future__ import annotations

import copy
import json

import pytest

from backend.liquidcooling.run_manager import (
    LiquidRunManager, RunConflictError, RunInputError, RunNotFoundError,
)
from backend.tests.test_hybrid_air_heat_handoff import _air, _project, _stls
from backend.tests.test_liquid.test_dae_runtime import case
from backend.tests.test_liquid.test_hybrid_heat_audit import coupled_case


def _bind_stls(air, tri):
    import hashlib
    air = copy.deepcopy(air)
    air['geometryExport']['stlFiles'] = {
        f'stl_{index:03d}': {'path': path.name, 'objectPath': None, 'kind': 'grouped_ascii',
                             'contentHash': hashlib.sha256(path.read_bytes()).hexdigest()}
        for index, path in enumerate(sorted(tri.glob('*.stl')))
    }
    return air


def _complete(manager, run_id):
    for multiplier in (1.2, .8, 1.0):
        manager.set_inputs(run_id, heat_multiplier=multiplier)
        status = manager.operate(run_id, 'step')
    assert status['status'] == 'completed'
    return status


@pytest.fixture(scope='module')
def coupled(tmp_path_factory):
    """One completed coupled run, persisted by a manager that is then dropped."""
    from backend.aircooling.runner.hybrid_air_heat_handoff import cfdproj_content_hash
    project = _project()
    raw = coupled_case()
    raw['source_project']['sha256'] = cfdproj_content_hash(project)
    raw['source_project']['schema_version'] = project['schemaVersion']
    case_dir = tmp_path_factory.mktemp('case')
    live = LiquidRunManager()
    run_id = live.create(raw, case_dir=case_dir)['run_id']
    _complete(live, run_id)
    return {'project': project, 'case_dir': case_dir, 'run_id': run_id, 'live': live,
            'run_dir': case_dir / 'Liquid' / 'log' / f'run-{run_id}'}


def _without_newton(frame):
    return {k: v for k, v in frame.items() if k != 'newton'}


def test_restored_run_serves_the_live_session_result_input_checkpoint_and_audit(coupled):
    live, run_id = coupled['live'], coupled['run_id']
    restored = LiquidRunManager()
    status = restored.restore(run_id, case_dir=coupled['case_dir'])
    expected = live.get(run_id)
    assert status['restored'] is True and status['read_only'] is True
    assert status['status'] == 'completed' and status['run_id'] == run_id
    assert status['config_hash'] == expected['config_hash']
    assert status['metadata'] == expected['metadata']
    assert status['project_revision'] == expected['project_revision']
    assert _without_newton(status['frame']) == _without_newton(expected['frame'])
    assert restored.result(run_id) == live.result(run_id)
    assert restored.executed_input(run_id) == live.executed_input(run_id)
    assert restored.checkpoint(run_id) == live.checkpoint(run_id)
    frames, live_frames = restored.frames(run_id)['frames'], live.frames(run_id)['frames']
    assert [f['seq'] for f in frames] == [f['seq'] for f in live_frames] == [0, 1, 2, 3]
    assert [_without_newton(f) for f in frames] == [_without_newton(f) for f in live_frames]
    assert restored.heat_audit(run_id, project_uid='project-stable-uid') == \
        live.heat_audit(run_id, project_uid='project-stable-uid')


def test_hybrid_air_compiles_from_a_saved_run_after_restart(coupled, tmp_path):
    project, run_id = coupled['project'], coupled['run_id']
    tri = _stls(tmp_path)
    air = _bind_stls(_air(project), tri)
    expected = coupled['live'].compile_hybrid_air_heat(
        run_id, project=project, air_cfdrun=copy.deepcopy(air), tri_surface_dir=tri, sample_index=1)
    fresh = LiquidRunManager()
    with pytest.raises(RunInputError, match='not loaded'):
        fresh.compile_hybrid_air_heat(run_id, project=project, air_cfdrun=copy.deepcopy(air),
                                      tri_surface_dir=tri, sample_index=1)
    got = fresh.compile_hybrid_air_heat(
        run_id, project=project, air_cfdrun=copy.deepcopy(air), tri_surface_dir=tri,
        sample_index=1, case_dir=coupled['case_dir'])
    assert got == expected
    assert fresh.get(run_id)['read_only'] is True
    # The project content hash is still enforced on a restored run.
    edited = copy.deepcopy(project)
    edited['name'] = 'edited after the liquid run'
    with pytest.raises(ValueError, match='contentHash|sha256'):
        fresh.compile_hybrid_air_heat(run_id, project=edited, air_cfdrun=_bind_stls(_air(edited), tri),
                                      tri_surface_dir=tri, sample_index=1, case_dir=coupled['case_dir'])
    with pytest.raises(RunInputError, match='not loaded and has no saved run'):
        LiquidRunManager().compile_hybrid_air_heat(
            '0' * 32, project=project, air_cfdrun=air, tri_surface_dir=tri, sample_index=1,
            case_dir=coupled['case_dir'])


def test_restored_runs_are_read_only_and_never_rewrite_their_files(coupled):
    before = {p.name: p.read_bytes() for p in coupled['run_dir'].iterdir()}
    manager = LiquidRunManager()
    run_id = coupled['run_id']
    manager.restore(run_id, case_dir=coupled['case_dir'])
    for call in (lambda: manager.set_inputs(run_id, heat_multiplier=2.),
                 lambda: manager.equilibrium(run_id),
                 lambda: manager.operate(run_id, 'step'),
                 lambda: manager.operate(run_id, 'start'),
                 lambda: manager.operate(run_id, 'cancel')):
        with pytest.raises(RunConflictError, match='read-only|status'):
            call()
    assert {p.name: p.read_bytes() for p in coupled['run_dir'].iterdir()} == before


def test_restore_is_idempotent_and_never_replaces_a_live_record(coupled):
    live, run_id = coupled['live'], coupled['run_id']
    again = live.restore(run_id, case_dir=coupled['case_dir'])
    assert again['restored'] is False and again['read_only'] is False
    manager = LiquidRunManager()
    first = manager.restore(run_id, case_dir=coupled['case_dir'])
    second = manager.restore(run_id, case_dir=coupled['case_dir'])
    assert first['restored'] is True and second['restored'] is False
    assert second['read_only'] is True


def _copy_run(coupled, tmp_path):
    import shutil
    target = tmp_path / 'case'
    shutil.copytree(coupled['case_dir'], target)
    return target, target / 'Liquid' / 'log' / f"run-{coupled['run_id']}"


def _edit(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize('name,change,error,match', [
    ('executed.liquidrun', lambda d: d['fluid'].update(rho_kg_m3=d['fluid']['rho_kg_m3'] + 1),
     RunInputError, 'executed'),
    ('executed-inputs.json', lambda d: d.update(executed_config_hash='0' * 64), RunInputError, 'executed'),
    ('executed-inputs.json', lambda d: d.update(original_config_hash='0' * 64), RunInputError, 'original'),
    ('input.liquidrun', lambda d: d['fluid'].update(rho_kg_m3=d['fluid']['rho_kg_m3'] + 1),
     RunInputError, 'config hash'),
    ('result.json', lambda d: d.update(config_hash='0' * 64), RunInputError, 'config hash'),
    ('result.json', lambda d: d.update(run_id='0' * 32), RunInputError, 'run id'),
    ('result.json', lambda d: d.update(status='failed'), RunInputError, 'status'),
    ('result.json', lambda d: d['time_s'].pop(), RunInputError, 'sample'),
    ('input.json', lambda d: d.update(config_hash='0' * 64), RunInputError, 'config hash'),
    ('checkpoint.json', lambda d: d.update(run_status='paused'), RunConflictError, 'paused'),
    ('checkpoint.json', lambda d: d['heat_multipliers'].__setitem__(1, 5.0), RunInputError, 'heat multipliers'),
])
def test_restore_rejects_tampered_or_inconsistent_artifacts(coupled, tmp_path, name, change, error, match):
    case_dir, run_dir = _copy_run(coupled, tmp_path)
    _edit(run_dir / name, change)
    with pytest.raises(error, match=match):
        LiquidRunManager().restore(coupled['run_id'], case_dir=case_dir)


@pytest.mark.parametrize('missing', ['executed-inputs.json', 'executed.liquidrun', 'input.liquidrun',
                                     'checkpoint.json', 'result.json', 'input.json'])
def test_restore_requires_every_persisted_artifact(coupled, tmp_path, missing):
    case_dir, run_dir = _copy_run(coupled, tmp_path)
    (run_dir / missing).unlink()
    with pytest.raises(RunInputError, match=missing):
        LiquidRunManager().restore(coupled['run_id'], case_dir=case_dir)


def test_restore_refuses_unknown_and_unsafe_run_ids(tmp_path):
    manager = LiquidRunManager()
    with pytest.raises(RunNotFoundError):
        manager.restore('0' * 32, case_dir=tmp_path)
    for bad in ('../x', 'a/b', '', 'x' * 65):
        with pytest.raises(RunInputError):
            manager.restore(bad, case_dir=tmp_path)


def test_a_cancelled_run_restores_for_viewing_but_never_hands_off_heat(tmp_path):
    manager = LiquidRunManager()
    run_id = manager.create(case(), case_dir=tmp_path)['run_id']
    manager.set_inputs(run_id, heat_multiplier=1.1)
    manager.operate(run_id, 'step')
    manager.operate(run_id, 'cancel')
    fresh = LiquidRunManager()
    status = fresh.restore(run_id, case_dir=tmp_path)
    assert status['status'] == 'cancelled' and status['frame']['seq'] == 1
    assert fresh.result(run_id)['completed'] is False
    with pytest.raises(RunInputError, match='completed'):
        fresh.compile_hybrid_air_heat(run_id, project={}, air_cfdrun={}, tri_surface_dir=tmp_path,
                                      sample_index=0)
