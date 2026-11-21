"""Thread-owned close/restart without launching a real browser or CFD process."""
import io
import json
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class FakePlaywright:
    def __init__(self):
        self.events = []
        self.started = threading.Event()
        self.release_command = threading.Event()
        self.release_close = threading.Event()
        self.release_close.set()
        self.close_started = threading.Event()
        self.generations = 0
        self.fail_context_close = False

    def sync(self):
        fake = self
        generation = self.generations = self.generations + 1
        owner = threading.get_ident()

        class Page:
            def on(self, *_): pass
            def goto(self, *_args, **_kwargs): pass
            def wait_for_function(self, *_args, **_kwargs): pass
            def wait_for_timeout(self, *_): pass
            def evaluate(self, script):
                if script == "blocked":
                    fake.started.set()
                    assert fake.release_command.wait(2)
                    return {"finished": True}
                if script in ("native-callback", "native-during-shutdown"):
                    if script == "native-during-shutdown":
                        with server._browser_init_lock:
                            server._browser_state.closing = True
                            server._browser_state.queue.put(None)
                    return server._release_idle_software_browser()
                if "WEBGL_debug_renderer_info" in script:
                    return "mock SwiftShader"
                fake.events.append((generation, "evaluate", owner, script))
                return {"success": True}

        class Context:
            def add_init_script(self, *_): pass
            def add_cookies(self, *_): pass
            def new_page(self): return Page()
            def close(self):
                fake.close_started.set()
                assert fake.release_close.wait(2)
                fake.events.append((generation, "context.close", threading.get_ident()))
                if fake.fail_context_close:
                    raise RuntimeError("mock context cleanup failure")

        class Browser:
            def new_context(self, **_): return Context()
            def close(self):
                fake.events.append((generation, "browser.close", threading.get_ident()))

        class Playwright:
            chromium = SimpleNamespace(launch=lambda **_: Browser())
            def start(self):
                fake.events.append((generation, "start", owner))
                return self
            def stop(self):
                fake.events.append((generation, "pw.stop", threading.get_ident()))

        return Playwright()


@pytest.fixture
def fake_browser(monkeypatch):
    fake = FakePlaywright()
    module = ModuleType("playwright.sync_api")
    module.sync_playwright = fake.sync
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(server, "_browser_state", None)
    monkeypatch.setattr(server, "_browser_cmd_q", None)
    monkeypatch.setattr(server, "_browser_thread", None)
    monkeypatch.setattr(server, "_browser_ready", threading.Event())
    monkeypatch.setattr(server, "_BROWSER_HEADLESS", True)
    monkeypatch.setattr(server, "AUTH_MGR", None)
    monkeypatch.setenv("DCDTUNI_BROWSER_SOFTWARE", "1")
    monkeypatch.delenv("DCDTUNI_BROWSER_MAX_FPS", raising=False)
    yield fake
    fake.release_command.set()
    fake.release_close.set()
    server._shutdown_browser()


HANDLER = SimpleNamespace(server=SimpleNamespace(server_address=("127.0.0.1", 1)))


def test_close_on_owner_thread_clears_state_and_next_request_starts_fresh(fake_browser):
    assert server._ensure_browser(HANDLER)
    first = server._browser_state
    assert server._browser_call("exec_js", "plain") == {"success": True}
    assert server._close_browser(timeout=1)["closed"] is True
    assert not first.ready.is_set()
    assert server._browser_state is None
    assert server._browser_cmd_q is None
    assert server._browser_thread is None
    assert not server._browser_ready.is_set()
    cleanup = [event for event in fake_browser.events if event[1] in ("context.close", "browser.close", "pw.stop")]
    assert [event[1] for event in cleanup] == ["context.close", "browser.close", "pw.stop"]
    assert {event[2] for event in cleanup} == {first.thread.ident}
    assert server._ensure_browser(HANDLER)
    second = server._browser_state
    assert second is not first and second.queue is not first.queue
    assert server._browser_call("exec_js", "plain") == {"success": True}
    assert fake_browser.generations == 2


def test_timed_out_real_command_remains_active_and_close_does_not_interrupt(fake_browser):
    assert server._ensure_browser(HANDLER)
    state = server._browser_state
    with pytest.raises(TimeoutError):
        server._browser_call("exec_js", "blocked", timeout=.02)
    assert fake_browser.started.is_set()
    assert state.admitted == 1
    result = server._close_browser(timeout=.02)
    assert result == {"ok": True, "closed": False, "status": "active", "pending": 1, "operation": "exec_js"}
    assert not state.closing and not fake_browser.close_started.is_set()
    fake_browser.release_command.set()
    deadline = time.monotonic() + 1
    while state.admitted and time.monotonic() < deadline:
        state.stopped.wait(.005)
    assert state.admitted == 0
    assert server._close_browser(timeout=1)["closed"] is True


def test_native_request_awaited_by_page_is_skipped_without_deadlock(fake_browser):
    assert server._ensure_browser(HANDLER)
    result = server._browser_call("exec_js", "native-callback", timeout=1)
    assert result["status"] == "active" and result["closed"] is False
    assert not fake_browser.close_started.is_set()
    assert server._browser_state.ready.is_set()
    operations = [event[3] for event in fake_browser.events if event[1] == "evaluate"]
    assert operations[-2:] == [
        "() => window.__dcdtuniAutomationFrames.resume()",
        "() => window.__dcdtuniAutomationFrames.pause()"]


def test_pending_queue_and_startup_are_not_idle(monkeypatch):
    state = server._BrowserWorkerState()
    monkeypatch.setattr(server, "_browser_state", state)
    assert server._close_browser(timeout=.01)["status"] == "active"
    state.ready.set()
    state.admitted = 2
    assert server._close_browser(timeout=.01)["pending"] == 2
    assert state.queue.empty() and not state.closing


def test_closing_generation_blocks_new_admission_and_restart_waits_for_resource_cleanup(fake_browser):
    assert server._ensure_browser(HANDLER)
    old = server._browser_state
    fake_browser.release_close.clear()
    result = server._close_browser(timeout=.02)
    assert result["status"] == "closing" and not result["closed"]
    assert fake_browser.close_started.is_set()
    with pytest.raises(RuntimeError, match="closing"):
        server._browser_call("exec_js", "plain")
    fresh = []
    waiting = threading.Thread(target=lambda: fresh.append(server._ensure_browser(HANDLER)))
    waiting.start()
    assert fake_browser.generations == 1
    fake_browser.release_close.set()
    waiting.join(1)
    assert fresh == [True]
    assert old.stopped.is_set()
    assert server._browser_state is not old
    assert fake_browser.generations == 2
    actions = [(event[0], event[1]) for event in fake_browser.events]
    assert actions.index((1, "pw.stop")) < actions.index((2, "start"))


def test_stale_generation_cleanup_cannot_clear_new_queue_ready_or_thread(monkeypatch):
    old = server._BrowserWorkerState()
    fresh = server._BrowserWorkerState()
    fresh.ready.set()
    fresh.thread = object()
    old_waiter = threading.Event()
    old_result = {}
    old.queue.put(("exec_js", (), old_waiter, old_result))
    fresh.queue.put("new-operation")
    monkeypatch.setattr(server, "_browser_state", fresh)
    monkeypatch.setattr(server, "_browser_cmd_q", fresh.queue)
    monkeypatch.setattr(server, "_browser_ready", fresh.ready)
    monkeypatch.setattr(server, "_browser_thread", fresh.thread)
    server._finish_browser_worker(old, [])
    assert old.stopped.is_set() and old_waiter.is_set()
    assert "error" in old_result
    assert server._browser_state is fresh
    assert server._browser_cmd_q is fresh.queue
    assert server._browser_ready is fresh.ready and fresh.ready.is_set()
    assert server._browser_thread is fresh.thread
    assert fresh.queue.get_nowait() == "new-operation"


def test_cleanup_failure_attempts_all_resources_and_fences_restart(fake_browser):
    assert server._ensure_browser(HANDLER)
    fake_browser.fail_context_close = True
    result = server._close_browser(timeout=1)
    assert result["status"] == "failed" and not result["closed"]
    assert "context" in result["error"]
    assert [event[1] for event in fake_browser.events if event[1] in ("context.close", "browser.close", "pw.stop")] == [
        "context.close", "browser.close", "pw.stop"]
    assert server._ensure_browser(HANDLER) is False
    assert fake_browser.generations == 1


@pytest.mark.parametrize("software, headless", [(False, True), (False, False)])
def test_automatic_release_never_closes_hardware_worker(monkeypatch, software, headless):
    state = server._BrowserWorkerState()
    state.ready.set()
    state.software, state.headless = software, headless
    monkeypatch.setattr(server, "_browser_state", state)
    assert server._release_idle_software_browser()["status"] == "not_applicable"
    assert not state.closing and state.queue.empty()


@pytest.mark.parametrize("session, status", [(None, None), ({"role": "viewer"}, 403), ({"role": "admin"}, 200)])
def test_close_route_is_authenticated_and_does_not_lazily_launch(monkeypatch, session, status):
    handler = object.__new__(server.STLServerHandler)
    handler.path = "/api/browser/close"
    replies, closes = [], []
    monkeypatch.setattr(server, "_require_auth", lambda _: session)
    monkeypatch.setattr(server, "_send_json", lambda _, code, body: replies.append((code, body)))
    monkeypatch.setattr(server, "_close_browser", lambda: closes.append(True) or {"ok": True, "closed": True, "status": "closed"})
    monkeypatch.setattr(server, "_ensure_browser", lambda _: pytest.fail("close must never start a worker"))
    server.STLServerHandler.do_POST(handler)
    assert bool(closes) == (status == 200)
    assert [reply[0] for reply in replies] == ([] if status is None else [status])


@pytest.mark.parametrize("route", ["run_mesh", "browser_full_pipeline"])
def test_native_cfd_popen_happens_after_idle_software_worker_resource_release(fake_browser, monkeypatch, tmp_path, route):
    assert server._ensure_browser(HANDLER)
    events = []

    class ImmediateThread:
        def __init__(self, *, target, daemon): self.target = target
        def start(self): self.target()

    class Proc:
        def __init__(self):self.stdin=io.TextIOWrapper(io.BytesIO(),encoding="utf-8")
        stdout = io.StringIO("")
        returncode = 1
        def wait(self): pass

    def popen(*_args, **_kwargs):
        assert server._browser_state is None
        assert [event[1] for event in fake_browser.events if event[1] in ("context.close", "browser.close", "pw.stop")] == [
            "context.close", "browser.close", "pw.stop"]
        events.append("popen")
        return Proc()

    monkeypatch.setattr(server, "_start_job_worker", lambda target: target())
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    body = {"cfdrunJson": "{}", "meshOnly": True, "projPath": "mock.cfdproj"}
    monkeypatch.setattr(server, "_read_body", lambda _, **kwargs:
        (body, json.dumps(body).encode()) if kwargs.get("include_bytes") else body)
    monkeypatch.setattr(server, "_publish_case_inputs", lambda *_args, **_kwargs: (tmp_path / "project.cfdrun", "a" * 64))
    from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
    snapshot=CfdrunSnapshot(b'{"schemaVersion":2}',tmp_path,tmp_path/"model/project.cfdrun",stl_batch_sha256=surface_batch_sha256(tmp_path))
    monkeypatch.setattr(server,"_publish_case_input_snapshot",lambda *_args,**_kwargs:snapshot)
    monkeypatch.setattr(server, "DCTWIN_AVAILABLE", True)
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    monkeypatch.setattr(server, "_send_json", lambda *_: None)
    if route == "browser_full_pipeline":
        js_calls = iter([{}, "{}", "mock.cfdrun"])
        def command(name, *_):
            assert server._browser_state.sequences == 1
            return {"success": True} if name == "load_project" else next(js_calls)
        monkeypatch.setattr(server, "_browser_call", command)
        server.handle_browser_full_pipeline(HANDLER)
    else:
        server.handle_run_mesh(HANDLER)
    assert events == ["popen"]


def test_multi_command_export_sequence_is_busy_even_between_individual_calls(fake_browser):
    assert server._ensure_browser(HANDLER)
    state = server._browser_state
    with server._browser_sequence():
        assert server._browser_call("exec_js", "plain") == {"success": True}
        assert state.admitted == 0
        assert server._close_browser(timeout=.01) == {
            "ok": True, "closed": False, "status": "active", "pending": 1, "operation": "export_sequence"}
        assert not fake_browser.close_started.is_set()
        assert server._browser_call("exec_js", "plain") == {"success": True}
    assert state.sequences == 0
    assert server._close_browser(timeout=1)["closed"] is True


def test_unexpected_worker_exit_fences_new_calls_before_cleanup_finishes(monkeypatch):
    state = server._BrowserWorkerState()
    entered, release = threading.Event(), threading.Event()
    class Context:
        def close(self):
            entered.set()
            assert release.wait(1)
    def broken_worker(_url, current, resources):
        resources["context"] = Context()
        current.ready.set()
        raise RuntimeError("mock startup interruption")
    monkeypatch.setattr(server, "_run_browser_worker", broken_worker)
    monkeypatch.setattr(server, "_browser_state", state)
    monkeypatch.setattr(server, "_browser_cmd_q", state.queue)
    monkeypatch.setattr(server, "_browser_ready", state.ready)
    thread = threading.Thread(target=server._browser_worker, args=("mock", state))
    thread.start()
    try:
        assert entered.wait(1)
        assert state.closing and not state.ready.is_set()
        with pytest.raises(RuntimeError, match="closing"):
            server._browser_call("exec_js", "plain")
    finally:
        release.set()
        thread.join(1)
    assert state.stopped.is_set()
    assert server._browser_state is None


def test_native_callback_never_waits_for_shutdown_already_queued_behind_it(fake_browser):
    assert server._ensure_browser(HANDLER)
    result = server._browser_call("exec_js", "native-during-shutdown", timeout=1)
    assert result["status"] == "active"
    assert result["closed"] is False
    assert server._close_browser(timeout=1)["closed"] is True


def test_automatic_release_closes_own_software_worker_on_xvfb(fake_browser, monkeypatch):
    monkeypatch.setattr(server, "_BROWSER_HEADLESS", False)
    monkeypatch.setattr(server, "_ensure_x_display", lambda: ":mock-owned-display")
    assert server._ensure_browser(HANDLER)
    state = server._browser_state
    assert state.software and not state.headless
    assert server._release_idle_software_browser()["closed"] is True
    assert state.stopped.is_set()
    assert server._browser_state is None
    assert [event[1] for event in fake_browser.events if event[1] in ("context.close", "browser.close", "pw.stop")] == [
        "context.close", "browser.close", "pw.stop"]


@pytest.mark.parametrize("software, explicit, expected", [
    (None, None, False), ("0", None, False), ("1", None, True),
    ("1", "0", False), ("1", "1", True), (None, "0", False), (None, "1", True),
])
def test_software_default_is_headless_and_explicit_or_hardware_defaults_are_preserved(monkeypatch, software, explicit, expected):
    for name, value in (("DCDTUNI_BROWSER_SOFTWARE", software), ("DCDTUNI_BROWSER_HEADLESS", explicit)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert server._browser_headless() is expected
