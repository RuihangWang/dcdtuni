"""Regression test for GET /api/browser/state (the get_state_summary handler).

Guards against the state.models -> state.construction rename being left
incomplete (commit 27f1820 missed get_state_summary; the frontend exposes only
state.construction, so a stale s.models read makes the handler throw
"Cannot read properties of undefined (reading 'map')").

Server-up gated, same as the other runtime suites — drives the live headless
browser channel via curl.
"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "frontend" / "scripts"))
# The port and the administrator login, as src/start_server.sh sets them up:
# DCDTUNI_PORT (default 8000, the launcher's default — this repo's launcher scans
# upward when a port is busy, so set it to whatever the banner printed), and the
# DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD pair, else the local-mode file
# src/server/auth-local/admin_credentials, else the pair written below.
from admin_login import base_url, login_payload, port as server_port  # noqa: E402


def _server_up(port: int = int(server_port())) -> bool:
    """True when a server of THIS user listens on the port.

    The default port is often held by another user's service on a shared host,
    and one dcdtuni server holds one global case dir, so somebody else's server
    counts as "not up" rather than as a server to drive. Where /proc/net/tcp is
    not readable, any listener counts.
    """
    want, listening = f"{port:04X}", None
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        listening = listening or []
        listening += [int(f[7]) for f in map(str.split, rows)
                      if f[3] == "0A" and f[1].rsplit(":", 1)[1] == want]
    if listening is not None:
        return os.getuid() in listening
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _server_up(),
    reason=f"no dcdtuni server of this user on :{server_port()} (set DCDTUNI_PORT)",
)

BASE = base_url()
COOKIE_JAR = "/tmp/stlgen_cookie_browser_state.txt"
CASE_DIR = REPO / "case" / "Base_leakage"
FIXTURE = CASE_DIR / "Base_leakage.cfdproj"


def _curl_json(args, max_time="30"):
    r = subprocess.run(
        ["curl", "-s", "-b", COOKIE_JAR, "--max-time", max_time, *args],
        check=True, capture_output=True, text=True,
    )
    return json.loads(r.stdout)


@pytest.fixture(scope="module", autouse=True)
def _session():
    login = subprocess.run([
        "curl", "-s", "-c", COOKIE_JAR, "-X", "POST", f"{BASE}/api/auth/login",
        "-H", "Content-Type: application/json",
        "--data-binary", json.dumps(login_payload(("admin", "WIN#@desk"))),
    ], check=True, capture_output=True, text=True)
    reply = json.loads(login.stdout or "{}")
    assert reply.get("ok"), f"login to {BASE} refused: {reply}"
    _curl_json([
        "-X", "POST", f"{BASE}/api/set-case-dir",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"caseDir": str(CASE_DIR)}),
    ])
    loaded = _curl_json([
        "-X", "POST", f"{BASE}/api/browser/load-proj",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"path": str(FIXTURE)}),
    ], max_time="120")
    assert loaded.get("success"), f"load-proj failed: {loaded}"
    yield


def test_browser_state_summary_uses_construction_not_models():
    """GET /api/browser/state must not throw on the renamed state field."""
    state = _curl_json(["-X", "GET", f"{BASE}/api/browser/state"])
    # The old s.models read would surface here as an error string, not a summary.
    assert "error" not in state, f"endpoint errored: {state.get('error')}"
    assert state["modelCount"] == 16, state["modelCount"]
    assert isinstance(state["models"], list)
    assert len(state["models"]) == 16
    # spot-check the per-model summary shape the handler builds from construction
    assert all("kind" in m and "id" in m for m in state["models"])
