"""W-SEC real entry-point contracts. Test stores/ports are explicitly isolated.

No product service, browser, shell, or real authentication store is accessed.
Tests use ephemeral HTTP listeners on loopback and the actual terminal callback
with a fake listener plus a PTY sentinel.
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import runpy
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

import server
from auth_manager import AuthManager
from security import CredentialsError, is_trusted_loopback, load_admin_credentials, resolve_bind_host


@pytest.fixture
def auth_store(tmp_path, monkeypatch):
    # --basetemp is required to stay inside repository _tmp.
    root = Path(__file__).resolve().parents[3]
    assert (root / '_tmp') in tmp_path.parents
    monkeypatch.delenv('DCDTUNI_ADMIN_USER', raising=False)
    monkeypatch.delenv('DCDTUNI_ADMIN_PASSWORD', raising=False)
    user = 'wsec_' + secrets.token_hex(4)
    password = secrets.token_urlsafe(32)
    store = AuthManager(tmp_path / 'auth', log_path=tmp_path / 'auth.log',
                        admin_credentials=(user, password))
    monkeypatch.setattr(server, 'AUTH_MGR', store)
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', False)
    monkeypatch.setattr(server, 'ENABLE_TERMINAL', False)
    monkeypatch.setattr(server, 'WORKER_TOKEN', None)
    return store, user, password


@contextmanager
def http_harness(monkeypatch, *, simulated_peer=None):
    class Handler(server.STLServerHandler):
        def setup(self):
            super().setup()
            if simulated_peer:
                self.client_address = (simulated_peer, self.client_address[1])
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
        connection = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=3)
        try:
            connection.request(method, path, body, h)
            response = connection.getresponse()
            data = response.read()
            parsed = json.loads(data) if data else {}
            return response.status, parsed, dict(response.getheaders())
        finally:
            connection.close()
    try:
        yield request, listener.server_port
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def login(request, user, password):
    status, body, headers = request('POST', '/api/auth/login',
                                   {'username': user, 'password': password})
    assert status == 200 and body['ok']
    return body['token'], headers['Set-Cookie'].split(';', 1)[0]


def test_real_http_loopback_requires_auth_and_login_logout(auth_store, monkeypatch):
    store, user, password = auth_store
    with http_harness(monkeypatch) as (request, port):
        status, body, _ = request('GET', '/api/auth/check')
        assert status == 200 and not body['authenticated'] and body['authRequired']
        assert request('GET', '/api/admin/users')[0] == 401
        token, cookie = login(request, user, password)
        assert server._auth_cookie_name_for_port(port) in cookie
        status, body, _ = request('GET', '/api/admin/users', headers={'Cookie': cookie})
        assert status == 200
        status, body, _ = request('GET', '/api/auth/check', headers={'Cookie': cookie})
        assert status == 200 and body['authenticated'] and body['role'] == 'admin'
        assert store.validate_session(token) is not None
        assert request('POST', '/api/auth/logout', headers={'Cookie': cookie})[0] == 200
        assert store.validate_session(token) is None
        assert request('GET', '/api/admin/users', headers={'Cookie': cookie})[0] == 401


@pytest.mark.parametrize('payload', [[], None, {'username': []}, {'password': 17}])
def test_real_login_rejects_nonobject_or_nonstrings(auth_store, monkeypatch, payload):
    with http_harness(monkeypatch) as (request, _):
        # None still arrives as an explicit valid JSON scalar here.
        if payload is None:
            payload = 17
        assert request('POST', '/api/auth/login', payload)[0] == 400


def test_auth_unconfigured_fails_closed_for_real_routes(monkeypatch):
    monkeypatch.setattr(server, 'AUTH_MGR', None)
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', True)
    with http_harness(monkeypatch) as (request, _):
        assert request('GET', '/api/admin/users')[0] == 503
        assert request('POST', '/api/auth/login', {'username': 'test'})[0] == 503
        status, body, _ = request('GET', '/api/auth/check')
        assert status == 200 and not body['authenticated'] and body['authRequired']


def test_explicit_http_loopback_trust_is_opt_in(auth_store, monkeypatch):
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', True)
    with http_harness(monkeypatch) as (request, _):
        assert request('GET', '/api/admin/users')[0] == 200
        _, body, _ = request('GET', '/api/auth/check')
        assert body['authenticated'] and body['role'] == 'admin'


def test_forwarded_header_does_not_grant_remote_admin(auth_store, monkeypatch):
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', True)
    # Production handler receives a simulated non-loopback peer address;
    # actual TCP harness remains loopback-only.
    with http_harness(monkeypatch, simulated_peer='203.0.113.9') as (request, _):
        h = {'X-Forwarded-For': '127.0.0.1'}
        assert request('GET', '/api/admin/users', headers=h)[0] == 401
        _, body, _ = request('GET', '/api/auth/check', headers=h)
        assert not body['authenticated']


def test_viewer_cannot_mutate_or_read_admin_routes(auth_store, monkeypatch):
    store, _, _ = auth_store
    viewer = 'viewer_' + secrets.token_hex(4)
    password = secrets.token_urlsafe(32)
    assert store.create_user(viewer, password, 'viewer')[0]
    with http_harness(monkeypatch) as (request, _):
        _, cookie = login(request, viewer, password)
        assert request('GET', '/api/admin/users', headers={'Cookie': cookie})[0] == 403
        assert request('POST', '/api/unknown', {}, {'Cookie': cookie})[0] == 403


def test_argument_defaults_are_real_startup_parser(monkeypatch):
    args = server.build_argument_parser().parse_args([])
    assert args.host == '127.0.0.1'
    assert not args.trust_loopback and not args.enable_terminal
    assert args.admin_credentials is None
    explicit = server.build_argument_parser().parse_args(
        ['--host', '0.0.0.0', '--trust-loopback', '--enable-terminal'])
    assert explicit.host == '0.0.0.0' and explicit.trust_loopback and explicit.enable_terminal
    assert resolve_bind_host('') == resolve_bind_host(None) == '127.0.0.1'
    monkeypatch.setattr(server, 'ENABLE_TERMINAL', False)
    # The default-off path returns before importing/creating any listener.
    assert server.start_websocket_terminal_server('', 0) is None


@pytest.mark.parametrize('peer', ['127.0.0.1', '::1', '::ffff:127.0.0.1'])
def test_loopback_only_if_explicit(peer):
    assert not is_trusted_loopback(peer, False)
    assert is_trusted_loopback(peer, True)


@pytest.mark.parametrize('peer', ['localhost', '127.0.0.1.evil', '203.0.113.1', '', None])
def test_no_hostname_or_invalid_loopback_trust(peer):
    assert not is_trusted_loopback(peer, True)


def test_bootstrap_without_credentials_writes_nothing(tmp_path):
    args = server.build_argument_parser().parse_args(['--auth-dir', str(tmp_path / 'store')])
    with pytest.raises(CredentialsError):
        server._initialize_auth(args, {})
    assert not args.auth_dir.exists()


@pytest.mark.parametrize('env', [
    {}, {'DCDTUNI_ADMIN_USER': 'example'}, {'DCDTUNI_ADMIN_PASSWORD': 'example'},
    {'DCDTUNI_ADMIN_USER': '', 'DCDTUNI_ADMIN_PASSWORD': 'example'},
    {'DCDTUNI_ADMIN_USER': 'example', 'DCDTUNI_ADMIN_PASSWORD': '   '},
    {'DCDTUNI_ADMIN_USER': 'line\nbreak', 'DCDTUNI_ADMIN_PASSWORD': 'example'},
])
def test_missing_or_partial_environment_credentials_fail(env):
    with pytest.raises(CredentialsError):
        load_admin_credentials(env, None)


def test_private_credential_file_and_random_worker_session(tmp_path, monkeypatch, capsys):
    username = 'boot_' + secrets.token_hex(4)
    password = secrets.token_urlsafe(32)
    credentials = tmp_path / 'credentials'
    credentials.write_text(username + '\n' + password + '\n')
    credentials.chmod(0o600)
    args = server.build_argument_parser().parse_args([
        '--auth-dir', str(tmp_path / 'store'),
        '--admin-credentials', str(credentials), '--trust-loopback'])
    store, token = server._initialize_auth(args, {})
    assert store.authenticate(username, password)[0]
    assert store.validate_session(token)['role'] == 'admin'
    # Reload the actual private test store; sessions persist as ordinary sessions.
    reloaded = AuthManager(args.auth_dir, admin_credentials=(username, password))
    assert reloaded.validate_session(token)
    second_store, second_token = server._initialize_auth(args, {})
    assert second_token != token
    monkeypatch.setattr(server, 'AUTH_MGR', store)
    monkeypatch.setattr(server, 'WORKER_TOKEN', token)
    cookies = server._worker_auth_cookies('http://127.0.0.1:8005/')
    assert len(cookies) == 1 and cookies[0]['value'] == token
    assert cookies[0]['httpOnly'] and cookies[0]['name'].endswith('_8005')
    captured = capsys.readouterr()
    assert password not in captured.out + captured.err
    assert token not in captured.out + captured.err
    trace = (args.auth_dir / 'server_auth.log').read_text()
    assert 'BROWSER_WORKER_SESSION: fingerprint=' in trace
    assert 'WARNING: --trust-loopback' in trace
    assert token not in trace and password not in trace


@pytest.mark.parametrize('mode', [0o644, 0o400, 0o666, 0o700])
def test_credential_file_requires_exact_0600(tmp_path, mode):
    p = tmp_path / 'credentials'
    p.write_text('example\n' + secrets.token_urlsafe(32) + '\n')
    p.chmod(mode)
    with pytest.raises(CredentialsError):
        load_admin_credentials({}, p)


def test_credentials_reject_symlink_fifo_and_malformed_file(tmp_path):
    p = tmp_path / 'credentials'
    p.write_text('example\n' + secrets.token_urlsafe(32) + '\n')
    p.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(p)
    with pytest.raises(CredentialsError):
        load_admin_credentials({}, link)
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo, 0o600)
    with pytest.raises(CredentialsError):
        load_admin_credentials({}, fifo)
    p.write_text('only-one-line\n')
    with pytest.raises(CredentialsError):
        load_admin_credentials({}, p)
    p.write_bytes(b'\xff\xfe')
    with pytest.raises(CredentialsError):
        load_admin_credentials({}, p)


def test_partial_environment_never_falls_back_to_file(tmp_path):
    p = tmp_path / 'credentials'
    p.write_text('example\n' + secrets.token_urlsafe(32) + '\n')
    p.chmod(0o600)
    with pytest.raises(CredentialsError):
        load_admin_credentials({'DCDTUNI_ADMIN_USER': 'example'}, p)


@pytest.mark.parametrize('origin', ['http://203.0.113.1:8005/', 'http://localhost:8005/',
                                  'file:///tmp/', 'http://127.0.0.1/'])
def test_worker_cookie_cannot_be_sent_to_arbitrary_origin(auth_store, monkeypatch, origin):
    store, user, _ = auth_store
    token = store.create_session(user, 'admin', '127.0.0.1')
    monkeypatch.setattr(server, 'WORKER_TOKEN', token)
    with pytest.raises((RuntimeError, ValueError)):
        server._worker_auth_cookies(origin)


def test_worker_requires_registered_session(auth_store, monkeypatch):
    monkeypatch.setattr(server, 'WORKER_TOKEN', secrets.token_urlsafe(32))
    with pytest.raises(RuntimeError):
        server._worker_auth_cookies('http://127.0.0.1:8005/')


class FakeWebSocket:
    def __init__(self, token=None, *, path=None):
        self.remote_address = ('127.0.0.1', 10001)
        self.request = SimpleNamespace(
            path=path if path is not None else '/?' + urlencode({'token': token}) if token else '/',
            headers={'X-Forwarded-For': '127.0.0.1'})
        self.closed = None
    def close(self, code, reason):
        self.closed = (code, reason)


def capture_real_terminal_callback(monkeypatch):
    import websockets.sync.server as ws_server
    callbacks = []
    @contextmanager
    def fake_serve(callback, host, port):
        callbacks.append((callback, host, port))
        yield SimpleNamespace(serve_forever=lambda: None)
    monkeypatch.setattr(ws_server, 'serve', fake_serve)
    thread = server.start_websocket_terminal_server('', 0, enabled=True)
    thread.join(timeout=3)
    assert not thread.is_alive() and len(callbacks) == 1
    assert callbacks[0][1] == '127.0.0.1'
    return callbacks[0][0]


@pytest.mark.parametrize('kind', ['missing', 'invalid', 'viewer', 'expired', 'bad-time', 'duplicate'])
def test_real_terminal_handler_denies_loopback_before_pty(auth_store, monkeypatch, kind):
    store, user, _ = auth_store
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', True)
    token = None
    path = None
    if kind in ('viewer', 'expired', 'bad-time', 'duplicate'):
        token = store.create_session(user, 'viewer' if kind == 'viewer' else 'admin', '127.0.0.1')
    if kind == 'invalid':
        token = secrets.token_urlsafe(32)
    if kind == 'expired':
        store._sessions[token]['created_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    if kind == 'bad-time':
        store._sessions[token]['created_at'] = 'invalid'
    if kind == 'duplicate':
        path = '/?' + urlencode([('token', token), ('token', token)])
    def forbidden_pty():
        raise AssertionError('Unauthenticated connection reached PTY')
    monkeypatch.setattr(server.pty, 'openpty', forbidden_pty)
    callback = capture_real_terminal_callback(monkeypatch)
    websocket = FakeWebSocket(token, path=path)
    callback(websocket)
    assert websocket.closed and websocket.closed[0] == 4001


def test_real_terminal_callback_accepts_valid_admin_without_shell(auth_store, monkeypatch):
    store, user, _ = auth_store
    token = store.create_session(user, 'admin', '127.0.0.1')
    class AuthorizedPtySentinel(Exception):
        pass
    def sentinel():
        raise AuthorizedPtySentinel()
    monkeypatch.setattr(server.pty, 'openpty', sentinel)
    callback = capture_real_terminal_callback(monkeypatch)
    websocket = FakeWebSocket(token)
    with pytest.raises(AuthorizedPtySentinel):
        callback(websocket)
    assert websocket.closed is None


def test_terminal_auth_unconfigured_cannot_start_listener(monkeypatch):
    monkeypatch.setattr(server, 'AUTH_MGR', None)
    with pytest.raises(RuntimeError):
        server.start_websocket_terminal_server('127.0.0.1', 0, enabled=True)
    websocket = FakeWebSocket(secrets.token_urlsafe(32))
    assert server._terminal_session(websocket) is None
    assert websocket.closed[0] == 4001


@pytest.mark.parametrize('created', ['', 'invalid',
    (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    datetime.now().isoformat()])
def test_session_invalid_time_cannot_authorize(auth_store, created):
    store, user, _ = auth_store
    token = store.create_session(user, 'admin', '127.0.0.1')
    store._sessions[token]['created_at'] = created
    assert store.validate_session(token) is None

@pytest.mark.parametrize('extra', [[], ['--host', '0.0.0.0']])
def test_actual_cli_missing_credentials_exits_before_bind(tmp_path, extra):
    env = {key: value for key, value in os.environ.items()
           if key not in ('DCDTUNI_ADMIN_USER', 'DCDTUNI_ADMIN_PASSWORD')}
    source = Path(__file__).resolve().parents[1] / 'server.py'
    store = tmp_path / 'uncreated-auth'
    completed = subprocess.run(
        [sys.executable, str(source), '--auth-dir', str(store), *extra],
        env=env, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 2
    assert 'administrator' in completed.stderr.lower()
    assert not store.exists()


def test_actual_cli_help_documents_opt_in_controls():
    source = Path(__file__).resolve().parents[1] / 'server.py'
    completed = subprocess.run([sys.executable, str(source), '--help'],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0
    assert '--trust-loopback' in completed.stdout
    assert '--enable-terminal' in completed.stdout
    assert '--admin-credentials' in completed.stdout

def test_real_worker_cookie_failure_redacts_secret_and_stops(auth_store, monkeypatch, capsys):
    store, user, password = auth_store
    token = store.create_session(user, 'admin', '127.0.0.1')
    monkeypatch.setattr(server, 'WORKER_TOKEN', token)
    monkeypatch.setattr(server, '_BROWSER_HEADLESS', True)
    monkeypatch.setattr(server, '_browser_state', None)
    monkeypatch.setenv('DCDTUNI_ADMIN_PASSWORD', password)
    launch_environment = {}
    class Context:
        def add_init_script(self, _script):
            pass
        def add_cookies(self, cookies):
            raise RuntimeError('driver echoed cookie=' + cookies[0]['value'])
        def new_page(self):
            raise AssertionError('Cookie failure must stop before creating page')
        def close(self):
            pass
    class Browser:
        def new_context(self, **_options):
            return Context()
        def close(self):
            pass
    class Playwright:
        def __init__(self):
            self.chromium = SimpleNamespace(launch=self.launch)
        def launch(self, **options):
            launch_environment.update(options['env'])
            return Browser()
        def start(self):
            return self
        def stop(self):
            pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api',
                        SimpleNamespace(sync_playwright=Playwright))
    state = server._BrowserWorkerState()
    server._browser_worker('http://127.0.0.1:8005/', state)
    captured = capsys.readouterr()
    assert 'DCDTUNI_ADMIN_PASSWORD' not in launch_environment
    assert state.start_error == 'Browser worker authentication installation failed'
    assert token not in captured.out + captured.err
    assert password not in captured.out + captured.err

@pytest.mark.parametrize('kind', ['missing', 'invalid', 'viewer', 'admin'])
def test_real_websocket_handshake_uses_production_terminal_auth(auth_store, monkeypatch, kind):
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect
    import websockets.sync.server as ws_server
    store, user, _ = auth_store
    monkeypatch.setattr(server, 'TRUST_LOOPBACK', True)
    token = None if kind == 'missing' else secrets.token_urlsafe(32)
    if kind in ('viewer', 'admin'):
        token = store.create_session(user, kind, '127.0.0.1')
    with monkeypatch.context() as listener_patch:
        callback = capture_real_terminal_callback(listener_patch)
    # The real library listener is restored after capturing the actual
    # application callback. Only the test harness binds a loopback port.
    reached_pty = threading.Event()
    class AuthorizedPtySentinel(Exception):
        pass
    def sentinel():
        reached_pty.set()
        raise AuthorizedPtySentinel()
    monkeypatch.setattr(server.pty, 'openpty', sentinel)
    def harness_callback(websocket):
        try:
            callback(websocket)
        except AuthorizedPtySentinel:
            websocket.close(1000, 'Authorized test handshake without shell')
    with ws_server.serve(harness_callback, '127.0.0.1', 0) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            query = '?' + urlencode({'token': token}) if token else ''
            address = 'ws://127.0.0.1:' + str(listener.socket.getsockname()[1]) + '/' + query
            with connect(address, open_timeout=3) as websocket:
                with pytest.raises(ConnectionClosed) as closed:
                    websocket.recv(timeout=3)
                assert closed.value.rcvd.code == (1000 if kind == 'admin' else 4001)
                assert reached_pty.is_set() == (kind == 'admin')
        finally:
            listener.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()

def test_private_file_must_belong_to_current_user(tmp_path, monkeypatch):
    credentials = tmp_path / 'credentials'
    credentials.write_text('example\n' + secrets.token_urlsafe(32) + '\n')
    credentials.chmod(0o600)
    original = os.fstat
    def foreign_owner(descriptor):
        value = original(descriptor)
        return SimpleNamespace(st_mode=value.st_mode, st_uid=os.getuid() + 1)
    monkeypatch.setattr(os, 'fstat', foreign_owner)
    with pytest.raises(CredentialsError):
        load_admin_credentials({}, credentials)


def test_actual_main_defaults_use_loopback_without_terminal(tmp_path, monkeypatch):
    # Execute the actual entrypoint with its real credentials/store, replacing
    # only listeners, process-global hooks and frontend asset readiness.
    # No platform service or browser is created.
    source = Path(__file__).resolve().parents[1] / 'server.py'
    username = 'entry_' + secrets.token_hex(4)
    password = secrets.token_urlsafe(32)
    monkeypatch.setenv('DCDTUNI_ADMIN_USER', username)
    monkeypatch.setenv('DCDTUNI_ADMIN_PASSWORD', password)
    monkeypatch.setattr(sys, 'argv', [str(source), '--auth-dir', str(tmp_path / 'store')])
    monkeypatch.setattr(os, 'chdir', lambda _path: None)
    import atexit
    import signal
    monkeypatch.setattr(atexit, 'register', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(signal, 'signal', lambda *_args: None)
    is_file = Path.is_file
    def ready_frontend(path):
        return True if 'frontend' in path.parts else is_file(path)
    monkeypatch.setattr(Path, 'is_file', ready_frontend)
    bound = []
    class NoListener:
        def __init__(self, address, handler):
            bound.append(address)
            assert issubclass(handler, http.server.SimpleHTTPRequestHandler)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def serve_forever(self):
            pass
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', NoListener)
    def forbidden_thread(_thread):
        raise AssertionError('Default entrypoint unexpectedly started a terminal/browser thread')
    monkeypatch.setattr(threading.Thread, 'start', forbidden_thread)
    namespace = runpy.run_path(str(source), run_name='__main__')
    assert bound == [('127.0.0.1', 8005)]
    assert namespace['TRUST_LOOPBACK'] is False
    assert namespace['ENABLE_TERMINAL'] is False
    assert namespace['AUTH_MGR'].validate_session(namespace['WORKER_TOKEN'])['role'] == 'admin'
