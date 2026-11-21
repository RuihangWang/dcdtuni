"""Saved liquid runs, session restore, the hybrid heat ledger and the retired
liquid endpoints, through the real HTTP handler.

Run from the repository root with ``--basetemp=_tmp/...`` (the auth store
lives under the repository's _tmp).
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import secrets
import threading
from pathlib import Path

import pytest

import server
import liquid_run_catalog as catalog
import liquid_runtime_api
from auth_manager import AuthManager


@pytest.fixture(scope='module')
def saved_case(tmp_path_factory):
    """A case with a completed DAE run (persisted, then the manager is dropped),
    a cancelled one, a legacy batch result and an old runtime checkpoint."""
    from backend.liquidcooling.run_manager import LiquidRunManager
    from backend.tests.test_liquid.test_hybrid_heat_audit import coupled_case
    case = tmp_path_factory.mktemp('liquid_case')
    manager = LiquidRunManager()
    completed = manager.create(coupled_case(), case_dir=case)['run_id']
    for multiplier in (1.2, .8, 1.0):
        manager.set_inputs(completed, heat_multiplier=multiplier)
        manager.operate(completed, 'step')
    cancelled = manager.create(coupled_case(), case_dir=case)['run_id']
    manager.operate(cancelled, 'cancel')
    legacy = case / 'Liquid' / 'log' / '2026-05-18-10-00-00_odil'
    legacy.mkdir(parents=True)
    (legacy / 'liquid_result.json').write_text(json.dumps({'converged': True}))
    old = case / 'Liquid' / 'log' / ('run-' + 'b' * 32)
    old.mkdir()
    (old / 'checkpoint.json').write_text(json.dumps({'format': 'dcdtuni-liquid-checkpoint-v1', 'run_status': 'completed'}))
    (old / 'result.json').write_text(json.dumps({'status': 'completed'}))
    (case / 'Liquid' / 'log' / 'run-notes').mkdir()  # no result: not a run
    # Deterministic order: legacy oldest, then old runtime, cancelled, completed newest.
    for offset, path in enumerate([legacy / 'liquid_result.json', old / 'result.json',
                                   case / 'Liquid' / 'log' / f'run-{cancelled}' / 'result.json',
                                   case / 'Liquid' / 'log' / f'run-{completed}' / 'result.json']):
        stamp = 1_800_000_000 + offset
        os.utime(path, (stamp, stamp))
    return {'case': case, 'completed': completed, 'cancelled': cancelled}


def test_catalog_lists_every_saved_run_newest_first_with_status_and_restorability(saved_case):
    runs = catalog.list_liquid_runs(saved_case['case'])
    names = [run['name'] for run in runs]
    assert names == [f"run-{saved_case['completed']}", f"run-{saved_case['cancelled']}",
                     'run-' + 'b' * 32, '2026-05-18-10-00-00_odil']
    done, cancelled, old, legacy = runs
    assert done['run_id'] == saved_case['completed'] and done['kind'] == 'dae'
    assert done['status'] == 'completed' and done['restorable'] is True and done['restore_blocker'] is None
    assert done['samples'] == {'accepted': 4, 'total': 4}
    assert done['analysis'] == {'mode': 'transient', 'initialization': 'steady'}
    assert done['source_project']['project_uid'] == 'project-stable-uid'
    assert done['accepted_time_s'] == 3.0 and done['dt_s'] == 1.0
    assert done['loaded'] is None and done['modified'] == 1_800_000_003
    assert cancelled['status'] == 'cancelled' and cancelled['restorable'] is True
    assert cancelled['samples'] == {'accepted': 1, 'total': 4}
    assert old['kind'] == 'runtime' and old['restorable'] is False and 'DAE' in old['restore_blocker']
    assert legacy['kind'] == 'legacy' and legacy['run_id'] is None and legacy['restorable'] is False
    loaded = catalog.list_liquid_runs(saved_case['case'],
                                      loaded={saved_case['completed']: {'status': 'completed', 'read_only': True}})
    assert loaded[0]['loaded'] == {'status': 'completed', 'read_only': True}


def test_catalog_reads_one_saved_run_and_refuses_unknown_or_unsafe_ids(saved_case):
    saved = catalog.saved_run_metadata(saved_case['case'], saved_case['completed'])
    assert saved['run']['run_id'] == saved_case['completed']
    assert saved['metadata']['solver'] == 'differentiable_newton_dae'
    assert saved['metadata']['cp_edge_ids'] == ['cold_plate_1']
    assert saved['receipt']['format'] == 'liquid-accepted-inputs/v1'
    assert saved['artifacts'] == {name: True for name in catalog.RUN_ARTIFACTS}
    for bad in ('../x', 'a b', 'x' * 65):
        with pytest.raises(catalog.CatalogError) as err:
            catalog.saved_run_metadata(saved_case['case'], bad)
        assert err.value.status == 400
    with pytest.raises(catalog.CatalogError) as err:
        catalog.saved_run_metadata(saved_case['case'], '0' * 32)
    assert err.value.status == 404


def test_catalog_without_a_case_or_liquid_log_is_empty(tmp_path):
    assert catalog.list_liquid_runs(tmp_path) == []
    with pytest.raises(catalog.CatalogError) as err:
        catalog.list_liquid_runs(None)
    assert err.value.status == 400


def _ledger_case(tmp_path, *, derived='hybrid_abc', ledger_run='hybrid_abc'):
    model = tmp_path / 'model'
    model.mkdir(parents=True)
    (model / 'x.cfdrun').write_text(json.dumps({'schemaVersion': 2, 'runId': derived}))
    (model / 'heat-ledger.json').write_text(json.dumps({
        'format': 'hybrid-air-heat-handoff/v2', 'air_derived_run_id': ledger_run,
        'liquid_run_id': 'f' * 32, 'sample_index': 3,
        'totals': {'total_it_W': 900.0, 'liquid_removed_W': 500.0, 'air_residual_W': 400.0}}))
    return tmp_path


def test_heat_ledger_is_read_with_its_pairing_to_the_active_manifest(tmp_path):
    with pytest.raises(catalog.CatalogError) as err:
        catalog.read_heat_ledger(tmp_path)
    assert err.value.status == 404 and 'hybridHeat' in str(err.value)
    case = _ledger_case(tmp_path / 'a')
    out = catalog.read_heat_ledger(case)
    assert out['path'] == 'model/heat-ledger.json'
    assert out['ledger']['totals']['air_residual_W'] == 400.0
    assert out['active_cfdrun'] == {'filename': 'x.cfdrun', 'run_id': 'hybrid_abc'}
    assert out['matches_active_cfdrun'] is True
    stale = catalog.read_heat_ledger(_ledger_case(tmp_path / 'b', derived='hybrid_new'))
    assert stale['matches_active_cfdrun'] is False
    bad = _ledger_case(tmp_path / 'c')
    (bad / 'model' / 'heat-ledger.json').write_text('{"format": "something-else"}')
    with pytest.raises(catalog.CatalogError) as err:
        catalog.read_heat_ledger(bad)
    assert err.value.status == 409


# ---------------------------------------------------------------- HTTP level

@pytest.fixture
def http_server(tmp_path, monkeypatch, saved_case):
    root = Path(__file__).resolve().parents[3]
    assert (root / '_tmp') in tmp_path.parents, 'run with --basetemp=_tmp/...'
    monkeypatch.delenv('DCDTUNI_ADMIN_USER', raising=False)
    monkeypatch.delenv('DCDTUNI_ADMIN_PASSWORD', raising=False)
    user, password = 'liquidcat_' + secrets.token_hex(4), secrets.token_urlsafe(32)
    store = AuthManager(tmp_path / 'auth', log_path=tmp_path / 'auth.log', admin_credentials=(user, password))
    monkeypatch.setattr(server, 'AUTH_MGR', store)
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', False)
    monkeypatch.setattr(server, 'WORKER_TOKEN', None)
    monkeypatch.setattr(server, 'CASE_DIR', saved_case['case'])
    # A freshly started server: nothing is loaded in memory.
    from backend.liquidcooling.run_manager import LiquidRunManager
    monkeypatch.setattr(liquid_runtime_api, '_MANAGER', LiquidRunManager())

    class Handler(server.STLServerHandler):
        def log_message(self, *_args):
            pass
    listener = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    listener.daemon_threads = True
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    cookie = {}

    def request(method, path, payload=None):
        body = json.dumps(payload) if payload is not None else None
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if cookie:
            headers['Cookie'] = cookie['value']
        connection = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=30)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            data = response.read()
            try:
                parsed = json.loads(data) if data else {}
            except ValueError:
                parsed = data.decode('utf-8', 'replace')
            return response.status, parsed, dict(response.getheaders())
        finally:
            connection.close()
    status, body, headers = request('POST', '/api/auth/login', {'username': user, 'password': password})
    assert status == 200 and body['ok']
    cookie['value'] = headers['Set-Cookie'].split(';', 1)[0]
    try:
        yield request
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_http_lists_restores_and_serves_a_run_after_a_restart(http_server, saved_case):
    run_id = saved_case['completed']
    status, listing, _ = http_server('GET', '/api/liquid/runs')
    assert status == 200 and listing['case'] == saved_case['case'].name
    assert listing['runs'][0]['run_id'] == run_id and listing['runs'][0]['loaded'] is None
    status, body, _ = http_server('GET', f'/api/liquid/runs/{run_id}')
    assert status == 404 and body['saved'] is True
    assert body['restore'] == f'/api/liquid/runs/{run_id}/restore'
    status, saved, _ = http_server('GET', f'/api/liquid/runs/{run_id}/saved')
    assert status == 200 and saved['run']['status'] == 'completed'
    status, restored, _ = http_server('POST', f'/api/liquid/runs/{run_id}/restore', {})
    assert status == 200 and restored['restored'] is True and restored['read_only'] is True
    assert restored['status'] == 'completed' and restored['frame']['seq'] == 3
    status, again, _ = http_server('POST', f'/api/liquid/runs/{run_id}/restore', {})
    assert status == 200 and again['restored'] is False
    status, result, _ = http_server('GET', f'/api/liquid/runs/{run_id}/result')
    assert status == 200 and result['completed'] is True and len(result['time_s']) == 4
    status, frames, _ = http_server('GET', f'/api/liquid/runs/{run_id}/frames?after=1')
    assert status == 200 and [f['seq'] for f in frames['frames']] == [2, 3]
    status, audit, _ = http_server('GET', f'/api/liquid/runs/{run_id}/heat-audit?project_uid=project-stable-uid')
    assert status == 200 and audit['identity_verified'] is True
    status, body, _ = http_server('POST', f'/api/liquid/runs/{run_id}/step', {})
    assert status == 409 and 'read-only' in body['error']
    status, listing, _ = http_server('GET', '/api/liquid/runs')
    assert listing['runs'][0]['loaded'] == {'status': 'completed', 'read_only': True}
    status, body, _ = http_server('POST', '/api/liquid/runs/' + 'b' * 32 + '/restore', {})
    assert status == 400 and 'DAE' in body['error']
    status, body, _ = http_server('POST', '/api/liquid/runs/' + '0' * 32 + '/restore', {})
    assert status == 404


def test_http_results_carry_the_topology_and_signed_server_flow(http_server, saved_case):
    status, result, _ = http_server('GET', f"/api/liquid/results?run=run-{saved_case['completed']}")
    assert status == 200
    edges = len(result['edge_ids'])
    for key in ('edge_from', 'edge_to', 'edge_types', 'edge_D_m'):
        assert len(result[key]) == edges, key
    assert len(result['node_positions_m']) == len(result['node_ids'])
    assert len(result['node_observable']) == len(result['node_ids'])
    assert len(result['edge_stagnant']) == edges
    servers = [s for rack in result['rack_summaries'] for s in rack['servers']]
    assert servers and all('signed_flow_kg_s' in s for s in servers)


def test_http_heat_ledger_endpoint(http_server, saved_case):
    status, body, _ = http_server('GET', '/api/hybrid/heat-ledger')
    assert status == 404 and body['code'] == 'heat_ledger_missing'
    _ledger_case(saved_case['case'])
    try:
        status, body, _ = http_server('GET', '/api/hybrid/heat-ledger')
        assert status == 200 and body['matches_active_cfdrun'] is True
        assert body['ledger']['liquid_run_id'] == 'f' * 32
    finally:
        import shutil
        shutil.rmtree(saved_case['case'] / 'model')


@pytest.mark.parametrize('path', ['/api/liquid/transient', '/api/run-coupled-sim'])
def test_retired_liquid_endpoints_answer_410_and_name_their_replacements(http_server, path):
    status, body, headers = http_server('POST', path, {})
    assert status == 410 and body['deprecated'] is True
    assert headers.get('Deprecation') == 'true'
    assert any('/api/liquid/runs' in item for item in body['replacement'])
    assert body['code'] == 'endpoint_retired'
