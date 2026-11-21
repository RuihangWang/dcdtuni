"""Runner cancellation must be one-shot even while Python is shutting down."""
import argparse
import importlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import types

import pytest


def handler_for(monkeypatch, tmp_path, runner):
    module = importlib.import_module("backend.aircooling.runner.run_cfd")
    cfdrun = tmp_path / "case.cfdrun"
    cfdrun.write_text("{}")
    monkeypatch.setattr(module, "parse_args", lambda: argparse.Namespace(
        from_cfdrun=str(cfdrun), expected_cfdrun_sha256=None,
        mesh_only=True, solve_only=False,
        cfdrun_stdin=False, expected_stl_batch_sha256=None,
        mesh_started_monotonic=None, mesh_deadline_monotonic=None,
    ))
    fake_module = types.ModuleType("backend.aircooling.runner.stlgen_runner")
    fake_module.STLGenRunner = runner
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)
    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, action: installed.__setitem__(sig, action))
    module.main()
    return installed[signal.SIGTERM], installed


class StubRunner:
    active = object()
    lookups = 0
    cleanups = 0
    callback = None
    cleanup_completed = False
    error = None

    def __init__(self, *_args, **_kwargs):
        pass

    def run_mesh_only(self):
        pass

    @classmethod
    def active_step_process(cls):
        cls.lookups += 1
        return cls.active

    @classmethod
    def _terminate_process_group(cls, _proc):
        cls.cleanups += 1
        if cls.callback is not None and cls.cleanups == 1:
            cls.callback(signal.SIGTERM, None)
        if cls.error is not None:
            raise cls.error
        cls.cleanup_completed = True


@pytest.fixture
def runner():
    class Runner(StubRunner):
        pass
    return Runner


def test_first_sigterm_disables_signal_before_child_lookup(monkeypatch, tmp_path, runner):
    handler, installed = handler_for(monkeypatch, tmp_path, runner)
    def lookup():
        assert installed[signal.SIGTERM] is signal.SIG_IGN
        runner.lookups += 1
        return runner.active
    monkeypatch.setattr(runner, "active_step_process", lookup)
    with pytest.raises(SystemExit) as stopped:
        handler(signal.SIGTERM, None)
    assert stopped.value.code == 143
    assert runner.cleanups == 1


def test_cleanup_reentrant_callback_does_not_interrupt_child_join(monkeypatch, tmp_path, runner):
    handler, _ = handler_for(monkeypatch, tmp_path, runner)
    runner.callback = handler
    with pytest.raises(SystemExit) as stopped:
        handler(signal.SIGTERM, None)
    assert stopped.value.code == 143
    assert runner.cleanups == 1
    assert runner.lookups == 1
    assert runner.cleanup_completed is True


def test_queued_callback_after_first_exit_is_noop(monkeypatch, tmp_path, runner):
    handler, _ = handler_for(monkeypatch, tmp_path, runner)
    with pytest.raises(SystemExit) as stopped:
        handler(signal.SIGTERM, None)
    assert stopped.value.code == 143
    assert handler(signal.SIGTERM, None) is None
    assert runner.cleanups == 1
    assert runner.lookups == 1


def test_no_active_child_still_exits_143(monkeypatch, tmp_path, runner):
    runner.active = None
    handler, installed = handler_for(monkeypatch, tmp_path, runner)
    with pytest.raises(SystemExit) as stopped:
        handler(signal.SIGTERM, None)
    assert stopped.value.code == 143
    assert runner.cleanups == 0
    assert installed[signal.SIGTERM] is signal.SIG_IGN


def test_cleanup_failure_is_not_hidden_or_retried(monkeypatch, tmp_path, runner):
    runner.error = RuntimeError("child cleanup failed")
    handler, _ = handler_for(monkeypatch, tmp_path, runner)
    with pytest.raises(RuntimeError, match="child cleanup failed"):
        handler(signal.SIGTERM, None)
    assert handler(signal.SIGTERM, None) is None
    assert runner.cleanups == 1
    assert runner.lookups == 1


def test_normal_runner_error_retains_existing_failure_path(monkeypatch, tmp_path, runner, capsys):
    def fail(self):
        raise RuntimeError("normal simulation error")
    monkeypatch.setattr(runner, "run_mesh_only", fail)
    with pytest.raises(SystemExit) as failed:
        handler_for(monkeypatch, tmp_path, runner)
    assert failed.value.code == 1
    assert "normal simulation error" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="POSIX process SIGTERM lifecycle")
@pytest.mark.parametrize("phase", ["child_cleanup", "atexit", "threading_shutdown"])
def test_real_sigterm_during_shutdown_keeps_first_exit(tmp_path, phase):
    """Deliver a second actual SIGTERM after entry into each shutdown phase."""
    cfdrun = tmp_path / "case.cfdrun"
    cfdrun.write_text("{}")
    src_root = Path(__file__).resolve().parents[2]
    program = r"""
import argparse, atexit, os, signal, sys, threading, types
from pathlib import Path
from backend.aircooling.runner import run_cfd
phase, cfdrun = sys.argv[1:]
class Runner:
    def __init__(self, *_args, **_kwargs): pass
    @classmethod
    def active_step_process(cls):
        return object() if phase == "child_cleanup" else None
    @classmethod
    def _terminate_process_group(cls, proc):
        print("CLEANUP_ENTER", flush=True)
        os.kill(os.getpid(), signal.SIGTERM)
        print("CLEANUP_JOINED", flush=True)
    def run_mesh_only(self):
        os.kill(os.getpid(), signal.SIGTERM)
fake = types.ModuleType("backend.aircooling.runner.stlgen_runner")
fake.STLGenRunner = Runner
sys.modules[fake.__name__] = fake
run_cfd.parse_args = lambda: argparse.Namespace(
    from_cfdrun=cfdrun, expected_cfdrun_sha256=None,
    mesh_only=True, solve_only=False,
    cfdrun_stdin=False, expected_stl_batch_sha256=None,
    mesh_started_monotonic=None, mesh_deadline_monotonic=None,
)
if phase == "atexit":
    def late_signal():
        os.kill(os.getpid(), signal.SIGTERM)
        print("ATEXIT_COMPLETED", flush=True)
    atexit.register(late_signal)
elif phase == "threading_shutdown":
    old_shutdown = threading._shutdown
    def late_signal():
        os.kill(os.getpid(), signal.SIGTERM)
        print("THREADING_SHUTDOWN_COMPLETED", flush=True)
        old_shutdown()
    threading._shutdown = late_signal
run_cfd.main()
"""
    env = dict(os.environ, PYTHONPATH=str(src_root))
    child = subprocess.run(
        [sys.executable, "-c", program, phase, str(cfdrun)],
        env=env, cwd=str(tmp_path), text=True, capture_output=True, timeout=10,
    )
    assert child.returncode == 143
    marker = {
        "child_cleanup": "CLEANUP_JOINED",
        "atexit": "ATEXIT_COMPLETED",
        "threading_shutdown": "THREADING_SHUTDOWN_COMPLETED",
    }[phase]
    assert marker in child.stdout
    assert "Traceback" not in child.stderr
    assert "Exception ignored" not in child.stderr
    assert "SystemExit" not in child.stderr
