"""`--app api`: the backend alone, for scripted runs (agent.scripted_api) — no editor page.

The API server must start where neither editor is available (no React build,
no archived STL_generate.html), answer every /api route the scripted run uses,
and refuse the browser automation channel, which needs an editor page to load.
Run from the repository root with ``--basetemp=_tmp/...`` (the auth fixture
keeps its store inside the repository's _tmp).
"""
from __future__ import annotations

import http.client
import http.server
import json
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import server
from auth_manager import AuthManager


@pytest.fixture
def api_mode(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    assert (root / '_tmp') in tmp_path.parents
    monkeypatch.delenv('DCDTUNI_ADMIN_USER', raising=False)
    monkeypatch.delenv('DCDTUNI_ADMIN_PASSWORD', raising=False)
    user, password = 'apimode_' + secrets.token_hex(4), secrets.token_urlsafe(32)
    store = AuthManager(tmp_path / 'auth', log_path=tmp_path / 'auth.log', admin_credentials=(user, password))
    monkeypatch.setattr(server, 'AUTH_MGR', store)
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', False)
    monkeypatch.setattr(server, 'ENABLE_TERMINAL', False)
    monkeypatch.setattr(server, 'WORKER_TOKEN', None)
    monkeypatch.setattr(server, 'APP_MODE', 'api')
    return user, password


@contextmanager
def harness():
    class Handler(server.STLServerHandler):
        def log_message(self, *_args):
            pass
    listener = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    listener.daemon_threads = True
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None, headers=None):
        body = json.dumps(payload) if payload is not None else None
        h = dict(headers or {})
        if body is not None:
            h['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=5)
        try:
            connection.request(method, path, body, h)
            response = connection.getresponse()
            data = response.read()
            try:
                parsed = json.loads(data) if data else {}
            except ValueError:
                parsed = data.decode('utf-8', 'replace')
            return response.status, parsed, dict(response.getheaders())
        finally:
            connection.close()
    try:
        yield request
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def _login(request, user, password):
    status, body, headers = request('POST', '/api/auth/login', {'username': user, 'password': password})
    assert status == 200 and body['ok']
    return headers['Set-Cookie'].split(';', 1)[0]


def test_parser_accepts_app_api():
    assert server.build_argument_parser().parse_args(['--app', 'api']).app == 'api'


def test_api_mode_starts_without_either_editor(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'REACT_DIST', tmp_path / 'no-react-build')
    monkeypatch.setattr(server, 'LEGACY_DIR', tmp_path / 'no-legacy-page')
    assert server.app_startup_error('api') is None
    assert 'STL_generate.html' in server.app_startup_error('html')
    assert 'npm run build' in server.app_startup_error('react')


def test_api_mode_serves_api_routes_and_no_pages(api_mode):
    user, password = api_mode
    with harness() as request:
        status, body, _ = request('GET', '/api/auth/check')
        assert status == 200 and body['authRequired']
        for path in ('/', '/index.html', '/STL_generate.html', '/login', '/js/cfdrun_build.js'):
            status, body, _ = request('GET', path)
            assert status == 404, path
            assert '--app api' in body['error'], path
        assert request('HEAD', '/js/cfdrun_build.js')[0] == 404
        cookie = _login(request, user, password)
        status, body, _ = request('GET', '/api/server-info', headers={'Cookie': cookie})
        assert status == 200 and body['app'] == 'api'


def test_api_mode_refuses_browser_automation_without_starting_a_browser(api_mode, monkeypatch):
    user, password = api_mode
    started = []
    monkeypatch.setattr(server, '_browser_worker', lambda *args, **kwargs: started.append(args))
    with harness() as request:
        cookie = _login(request, user, password)
        status, body, _ = request('GET', '/api/browser/state', headers={'Cookie': cookie})
        assert status == 409 and '--app api' in body['error']
        status, body, _ = request('POST', '/api/browser/exec-js', {'js': '1'}, headers={'Cookie': cookie})
        assert status == 409 and '--app react' in body['error']
    assert started == []
    assert server._ensure_browser(object()) is False
    assert started == []
