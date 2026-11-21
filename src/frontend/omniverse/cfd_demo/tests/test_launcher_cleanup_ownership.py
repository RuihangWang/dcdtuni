"""Real TCP listeners exercise launcher ownership across port reuse."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src/start_server.sh").is_file())
START = ROOT / "src/start_server.sh"
SERVER = """import pathlib, socket, sys, time
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", int(sys.argv[1])))
listener.listen()
pathlib.Path(sys.argv[2]).write_text(str(listener.getsockname()[1]))
time.sleep(60)
"""


def wait_file(path):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.02)
    raise AssertionError("controlled listener did not publish")


def stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)


def extract_helpers(source):
    port_lookup = source.split("port_own_pids() {", 1)[1].split("\n# 只释放", 1)[0]
    ownership = source.split("CLEANUP_LAUNCHER_PID=$$", 1)[1].split("\ncleanup() {", 1)[0]
    return ('CURRENT_USER="$(id -un)"\nport_own_pids() {' + port_lookup
            + "\nCLEANUP_LAUNCHER_PID=$$" + ownership)


@pytest.mark.parametrize("reuse", [False, True])
def test_cleanup_terminates_only_current_launcher_descendants(tmp_path, reuse):
    helpers = extract_helpers(START.read_text())
    server = tmp_path / "server.py"
    server.write_text(SERVER)
    ready, pid_file, go = (tmp_path / name for name in ("ready", "pid", "go"))
    script = tmp_path / "owner.sh"
    script.write_text(helpers + f'''
"{sys.executable}" "{server}" 0 "{ready}" &
owned=$!
echo "$owned" > "{pid_file}"
while [[ ! -f "{go}" ]]; do sleep .02; done
cleanup_owned_port "$(cat "{ready}")"
wait "$owned" 2>/dev/null || true
''')
    launcher = subprocess.Popen(["bash", str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    replacement = None
    owned = None
    try:
        port = int(wait_file(ready))
        owned = int(wait_file(pid_file))
        if reuse:
            os.kill(owned, signal.SIGTERM)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and Path("/proc", str(owned)).exists():
                time.sleep(0.02)
            assert not Path("/proc", str(owned)).exists()
            replacement = subprocess.Popen([
                sys.executable, str(server), str(port), str(tmp_path / "replacement"),
            ])
            assert int(wait_file(tmp_path / "replacement")) == port
        go.write_text("go")
        assert launcher.wait(timeout=8) == 0
        if reuse:
            assert replacement.poll() is None
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pass
        else:
            assert not Path("/proc", str(owned)).exists()
    finally:
        stop(replacement)
        # Keep the owner alive until its child is stopped on an assertion failure.
        if launcher.poll() is None and owned is not None:
            try:
                os.kill(owned, signal.SIGTERM)
            except ProcessLookupError:
                pass
        stop(launcher)


@pytest.mark.parametrize("status", [0, 7])
def test_empty_cleanup_preserves_original_exit_status(status):
    source = START.read_text()
    cleanup = source.split("cleanup() {", 1)[1].split("\ntrap cleanup", 1)[0]
    script = ("set -e\nOV_RUNTIME_DIRS=()\nCLEANUP_PORTS=()\ncleanup() {"
              + cleanup + f"\ntrap cleanup EXIT\nexit {status}\n")
    assert subprocess.run(["bash", "-c", script], check=False).returncode == status
