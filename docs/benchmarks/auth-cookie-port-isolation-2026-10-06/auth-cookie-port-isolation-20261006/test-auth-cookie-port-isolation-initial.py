"""Port-local cookies, using fake handlers/stores without HTTP or browser launches."""
import http.cookies
import http.cookiejar
import io
from email.message import Message
from types import SimpleNamespace
from urllib.request import Request

import pytest
import server
from test_browser_lifecycle import FakePlaywright


class AuthStore:
    def __init__(self, label):
        self.label = label
        self.sessions = {}
        self.validated = []
        self.deleted = []

    def authenticate(self, username, password):
        return (username == "fixture-user" and password == "fixture-password", {"role": "admin"})

    def check_ip_banned(self, ip):
        return False, ""

    def record_login_success(self, ip):
        pass

    def record_login_failure(self, ip):
        pass

    def log(self, message):
        pass

    def create_session(self, username, role, ip):
        token = f"{self.label}-fixture-{len(self.sessions)}"
        self.sessions[token] = {"username": username, "role": role}
        return token

    def validate_session(self, token):
        self.validated.append(token)
        session = self.sessions.get(token)
        return dict(session) if session else None

    def delete_session(self, token):
        self.deleted.append(token)
        return self.sessions.pop(token, None) is not None

    def seed(self, token):
        self.sessions[token] = {"username": "fixture-user", "role": "admin"}
        return token


class Handler:
    def __init__(self, port, cookie="", body=b""):
        self.server = SimpleNamespace(server_address=("127.0.0.1", port))
        self.headers = {"Cookie": cookie, "Content-Length": str(len(body)), "Host": "same-host.invalid"}
        self.client_address = ("192.0.2.1", 1234)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.response_headers = []
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass


def response_cookies(handler):
    cookie = http.cookies.SimpleCookie()
    for name, value in handler.response_headers:
        if name == "Set-Cookie":
            cookie.load(value)
    return cookie


def cookie_header(jar, port):
    request = Request(f"http://same-host.invalid:{port}/api/fixture")
    jar.add_cookie_header(request)
    return request.get_header("Cookie", "")


def absorb(jar, handler, port):
    headers = Message()
    for name, value in handler.response_headers:
        if name == "Set-Cookie":
            headers.add_header(name, value)
    jar.extract_cookies(SimpleNamespace(info=lambda: headers),
                        Request(f"http://same-host.invalid:{port}/api/auth/login"))


def login(monkeypatch, store, port, cookie=""):
    monkeypatch.setattr(server, "AUTH_MGR", store)
    handler = Handler(port, cookie, b'{"username":"fixture-user","password":"fixture-password"}')
    server.handle_auth_login(handler)
    assert handler.status == 200
    return handler


def test_two_ports_preserve_independent_sessions_in_one_host_cookie_jar(monkeypatch):
    main, integration = AuthStore("main"), AuthStore("integration")
    jar = http.cookiejar.CookieJar()
    first = login(monkeypatch, main, 8390)
    absorb(jar, first, 8390)
    second = login(monkeypatch, integration, 8396, cookie_header(jar, 8396))
    absorb(jar, second, 8396)
    assert {cookie.name for cookie in jar} == {"stlgen_session_8390", "stlgen_session_8396"}
    for store, port in [(main, 8390), (integration, 8396)]:
        monkeypatch.setattr(server, "AUTH_MGR", store)
        session = server._get_session(Handler(port, cookie_header(jar, port)))
        assert session["username"] == "fixture-user"
        assert session["_token"].startswith(store.label)


def test_login_preserves_existing_cookie_security_and_uses_bound_port(monkeypatch):
    store = AuthStore("own")
    handler = Handler(8396, body=b'{"username":"fixture-user","password":"fixture-password"}')
    handler.headers["Host"] = "untrusted.invalid:8390"
    monkeypatch.setattr(server, "AUTH_MGR", store)
    server.handle_auth_login(handler)
    cookie = response_cookies(handler)
    assert list(cookie) == ["stlgen_session_8396"]
    own = cookie["stlgen_session_8396"]
    assert own["path"] == "/" and own["httponly"] is True
    assert own["samesite"] == "Lax" and own["max-age"] == "43200"


def test_valid_scoped_cookie_has_priority_over_own_legacy(monkeypatch):
    store = AuthStore("own")
    store.seed("scoped")
    store.seed("legacy")
    monkeypatch.setattr(server, "AUTH_MGR", store)
    session = server._get_session(Handler(8396, "stlgen_session_8396=scoped; stlgen_session=legacy"))
    assert session["_token"] == "scoped"
    assert store.validated == ["scoped"]


@pytest.mark.parametrize("scoped_value", ["wrong-store", ""])
def test_present_invalid_scoped_cookie_cannot_downgrade_to_legacy(monkeypatch, scoped_value):
    store = AuthStore("own")
    store.seed("legacy")
    monkeypatch.setattr(server, "AUTH_MGR", store)
    assert server._get_session(Handler(8396, f"stlgen_session_8396={scoped_value}; stlgen_session=legacy")) is None
    assert "legacy" not in store.validated


def test_legacy_fallback_validates_only_in_own_store(monkeypatch):
    own, foreign = AuthStore("own"), AuthStore("foreign")
    own.seed("own-legacy")
    foreign.seed("foreign-legacy")
    monkeypatch.setattr(server, "AUTH_MGR", own)
    assert server._get_session(Handler(8396, "stlgen_session=own-legacy"))["_token"] == "own-legacy"
    assert server._get_session(Handler(8396, "stlgen_session=foreign-legacy")) is None


def test_scoped_logout_expires_only_own_namespace_and_preserves_other_port(monkeypatch):
    own, foreign = AuthStore("own"), AuthStore("foreign")
    own.seed("own-token")
    foreign.seed("foreign-token")
    monkeypatch.setattr(server, "AUTH_MGR", own)
    handler = Handler(8396, "stlgen_session_8396=own-token; stlgen_session_8390=foreign-token; stlgen_session=foreign-token")
    server.handle_auth_logout(handler)
    cookie = response_cookies(handler)
    assert list(cookie) == ["stlgen_session_8396"]
    assert cookie["stlgen_session_8396"]["max-age"] == "0"
    assert cookie["stlgen_session_8396"]["path"] == "/"
    assert own.deleted == ["own-token"]
    assert foreign.validate_session("foreign-token") is not None


def test_legacy_logout_expires_legacy_only_when_it_was_chosen(monkeypatch):
    own = AuthStore("own")
    own.seed("legacy")
    monkeypatch.setattr(server, "AUTH_MGR", own)
    handler = Handler(8396, "stlgen_session=legacy")
    server.handle_auth_logout(handler)
    cookie = response_cookies(handler)
    assert set(cookie) == {"stlgen_session", "stlgen_session_8396"}
    assert all(value["max-age"] == "0" for value in cookie.values())
    assert own.deleted == ["legacy"]


def test_own_legacy_cookie_migrates_on_login_and_cannot_resurrect_after_logout(monkeypatch):
    own = AuthStore("own")
    own.seed("legacy")
    jar = http.cookiejar.CookieJar()
    initial = Handler(8396)
    initial.send_header("Set-Cookie", "stlgen_session=legacy; Path=/")
    absorb(jar, initial, 8396)
    signed_in = login(monkeypatch, own, 8396, cookie_header(jar, 8396))
    absorb(jar, signed_in, 8396)
    assert {cookie.name for cookie in jar} == {"stlgen_session_8396"}
    signed_out = Handler(8396, cookie_header(jar, 8396))
    server.handle_auth_logout(signed_out)
    absorb(jar, signed_out, 8396)
    assert not list(jar)
    assert server._get_session(Handler(8396, cookie_header(jar, 8396))) is None


def test_login_never_clears_foreign_legacy_cookie(monkeypatch):
    own, foreign = AuthStore("own"), AuthStore("foreign")
    foreign.seed("foreign-legacy")
    handler = login(monkeypatch, own, 8396, "stlgen_session=foreign-legacy")
    assert list(response_cookies(handler)) == ["stlgen_session_8396"]
    assert foreign.validate_session("foreign-legacy") is not None


def test_worker_uses_http_page_port_not_terminal_ws_port(monkeypatch):
    fake = FakePlaywright()
    captured = []
    original_sync = fake.sync

    def sync():
        playwright = original_sync()
        original_launch = playwright.chromium.launch

        def launch(**kwargs):
            browser = original_launch(**kwargs)
            original_context = browser.new_context

            def new_context(**options):
                context = original_context(**options)
                context.add_cookies = lambda rows: captured.extend(rows)
                return context

            browser.new_context = new_context
            return browser

        playwright.chromium.launch = launch
        return playwright

    import sys
    from types import ModuleType
    module = ModuleType("playwright.sync_api")
    module.sync_playwright = sync
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    store = AuthStore("worker")
    monkeypatch.setattr(server, "AUTH_MGR", store)
    monkeypatch.setattr(server, "WS_PORT", 8397)
    monkeypatch.setattr(server, "_BROWSER_HEADLESS", True)
    monkeypatch.setattr(server, "_BROWSER_LOG_LINES", [])
    monkeypatch.setattr(server, "_browser_state", None)
    monkeypatch.setenv("DCDTUNI_BROWSER_SOFTWARE", "1")
    monkeypatch.delenv("DCDTUNI_BROWSER_MAX_FPS", raising=False)
    state = server._BrowserWorkerState()
    state.queue.put(None)
    server._browser_worker("http://127.0.0.1:8396/", state)
    assert len(captured) == 1
    assert captured[0]["name"] == "stlgen_session_8396"
    assert captured[0]["httpOnly"] is True and captured[0]["sameSite"] == "Lax"
    assert store.validate_session(captured[0]["value"]) is not None
