#!/usr/bin/env python3
"""
STL Generator Backend Server

The server runs inside the dcdtuni conda environment (via start_server.sh) and
invokes dcdtuni using the SAME Python interpreter — no conda run, no PYTHONPATH tricks.

Usage:
    ./src/start_server.sh --case-dir /path/to/case
    # Then open http://localhost:<port>/  (the React app; --app html for the old one)

    # Or set case dir later from the browser UI (POST /api/set-case-dir).
"""

import atexit
import asyncio
import base64
import binascii
import fcntl
import functools
import hashlib
import http.cookies
import http.server
import json
import logging
import math
import os
import pty
import re
import select
import shlex
import signal
import struct
import subprocess
import shutil
import sys
import threading
import tempfile
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from liquid_result_files import liquid_result_file, selected_liquid_result
from backend.aircooling.runner.case_execution_lock import (
    CaseExecutionBusyError,
    case_execution_lock,
)

# The assets BOTH editors use: the .cfdproj format modules and geometry builders
# (js/), the 1634-key dictionaries (i18n/), the vendored libraries and the PBR
# textures. React imports them from source; the legacy page loads them by URL.
# They were under web/ next to the legacy app, which made them look like its
# private files — they are not, and the cutover cannot delete web/ until they
# have moved out.
SHARED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "shared"

# The static root. Kept under the old name because a dozen call sites use it and
# renaming them all is churn for no behaviour change; what changed is WHERE it
# points.
HTML_DIR = SHARED_DIR

# The React app's build output. `/` and `/assets/*` come from here; every other
# static path still resolves under HTML_DIR, because the two apps SHARE those
# assets — js/ (the .cfdproj format modules and the geometry builders), i18n/,
# vendor/ and textures/ are imported by the React source, not duplicated into it.
# Splitting them out into src/frontend/shared/ is P4 of the cutover plan; until
# then this is the seam.
REACT_DIST = Path(__file__).resolve().parent.parent / "frontend" / "react" / "dist"

# The archived single-file editor. React replaced it and nothing here depends on
# it, but `--app html` still serves it: it is the only running reference for
# behaviour the port may not have reproduced, and the feature-parity audit is
# not finished. Read it, compare against it — do not develop in it.
LEGACY_DIR = Path(__file__).resolve().parent.parent / "frontend" / "web"

# The legacy page's OWN files. Everything else it asks for — js/, vendor/,
# i18n/, textures/ — comes from SHARED_DIR, because both editors always shared
# those; only the page, its 2D editor and its theme live in `web/`.
LEGACY_FILES = ("/STL_generate.html", "/room_2d_editor.js", "/benchmark.js")

# Which editor this process serves: `react` (default) or `html`. Set from
# `--app` at startup and NOT auto-detected — a missing React build should say so
# rather than quietly falling back to the archived editor.
APP_MODE = "react"

# ---------------------------------------------------------------------------
# Minimal server-side i18n helper
# ---------------------------------------------------------------------------
_I18N_DIR = HTML_DIR / "i18n"
_I18N_CACHE: dict = {}

def _load_lang(locale: str) -> dict:
    if locale not in _I18N_CACHE:
        p = _I18N_DIR / f"{locale}.json"
        _I18N_CACHE[locale] = json.loads(p.read_text()) if p.exists() else {}
    return _I18N_CACHE[locale]

def st(key: str, locale: str = "zh-CN", **kw) -> str:
    d = _load_lang(locale)
    fb = _load_lang("zh-CN")
    txt = d.get(key) or fb.get(key) or key
    for k, v in kw.items():
        txt = txt.replace(f"{{{k}}}", str(v))
    return txt

# ---------------------------------------------------------------------------
# Authentication System (auth_manager-backed)
# ---------------------------------------------------------------------------

from auth_manager import AuthManager

AUTH_MGR: AuthManager | None = None  # Initialized in __main__
AUTH_COOKIE_NAME = "stlgen_session"  # Legacy cookie, accepted only in this session store.
AUTH_PUBLIC_PATHS = frozenset({"/login", "/api/auth/login", "/api/auth/check", "/favicon.ico"})
VIEWER_POST_PATHS = frozenset({"/api/pp/init", "/api/auth/logout"})


def _get_client_ip(handler) -> str:
    """Extract client IP, respecting X-Forwarded-For if present."""
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0]


def _auth_cookie_name_for_port(port: int) -> str:
    """Cookies share a host across ports, so namespace this HTTP instance."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
        raise ValueError("Authentication requires the bound HTTP port")
    return f"{AUTH_COOKIE_NAME}_{port}"


def _auth_cookie_name(handler) -> str:
    # Use the bound listener, never the request's untrusted Host header.
    return _auth_cookie_name_for_port(handler.server.server_address[1])


def _auth_cookies(handler) -> http.cookies.SimpleCookie | None:
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(handler.headers.get("Cookie", ""))
    except http.cookies.CookieError:
        return None
    return cookies


def _expire_auth_cookie(handler, name: str) -> None:
    cookie = http.cookies.SimpleCookie()
    cookie[name] = ""
    cookie[name]["path"] = "/"
    cookie[name]["max-age"] = "0"
    handler.send_header("Set-Cookie", cookie[name].OutputString())


def _get_session(handler) -> dict | None:
    """Prefer this HTTP instance; legacy fallback must belong to our store."""
    if AUTH_MGR is None:
        return None
    cookies = _auth_cookies(handler)
    if cookies is None:
        return None
    cookie_name = _auth_cookie_name(handler)
    # A present invalid scoped cookie must not downgrade to a legacy session.
    if cookie_name not in cookies:
        cookie_name = AUTH_COOKIE_NAME
    morsel = cookies.get(cookie_name)
    if not morsel:
        return None
    session = AUTH_MGR.validate_session(morsel.value)
    if session:
        session["_token"] = morsel.value
        session["_cookie_name"] = cookie_name
    return session


def _require_auth(handler) -> dict | None:
    """Check auth; return session dict or send 401/302 and return None."""
    if AUTH_MGR is None:
        return {"username": "anonymous", "role": "admin", "_token": ""}
    session = _get_session(handler)
    if session:
        return session
    parsed = urlparse(handler.path)
    if parsed.path.startswith("/api/"):
        _send_json(handler, 401, {"error": "Authentication required"})
    else:
        _redirect_to_login(handler)
    return None


def _require_admin(handler) -> dict | None:
    """Check auth + admin role; return session or send 401/403 and return None."""
    session = _require_auth(handler)
    if session is None:
        return None
    if session.get("role") != "admin":
        _send_json(handler, 403, {"error": "Admin access required"})
        return None
    return session


_LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>STL Generator - Login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f1419;
    color: #e6e6e6;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }
  .login-card {
    background: #1a1f2e;
    border: 1px solid #2d3548;
    border-radius: 12px;
    padding: 40px;
    width: 100%;
    max-width: 400px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  .login-card h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 8px;
    color: #fff;
  }
  .login-card p.subtitle {
    font-size: 0.85rem;
    color: #8892a4;
    margin-bottom: 28px;
  }
  label {
    display: block;
    font-size: 0.8rem;
    color: #8892a4;
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  input[type="text"], input[type="password"] {
    width: 100%;
    padding: 12px 14px;
    background: #0f1419;
    border: 1px solid #2d3548;
    border-radius: 8px;
    color: #e6e6e6;
    font-size: 1rem;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 16px;
  }
  input[type="text"]:focus, input[type="password"]:focus {
    border-color: #4a9eff;
  }
  button {
    width: 100%;
    margin-top: 20px;
    padding: 12px;
    background: #4a9eff;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.2s;
  }
  button:hover { background: #3a8eef; }
  button:disabled { background: #3a5a8a; cursor: wait; }
  .error-msg {
    margin-top: 14px;
    padding: 10px 14px;
    background: #2d1a1a;
    border: 1px solid #5c2626;
    border-radius: 6px;
    color: #ff6b6b;
    font-size: 0.85rem;
    display: none;
  }
  .lock-icon {
    font-size: 2.5rem;
    margin-bottom: 16px;
  }
</style>
</head>
<body>
<div class="login-card">
  <div class="lock-icon">&#128274;</div>
  <h1>STL Generator</h1>
  <p class="subtitle">This server requires authentication to access.</p>
  <form id="loginForm">
    <label for="uname">Username</label>
    <input type="text" id="uname" name="username" autocomplete="username" autofocus required>
    <label for="pwd">Password</label>
    <input type="password" id="pwd" name="password" autocomplete="current-password" required>
    <button type="submit" id="submitBtn">Sign In</button>
  </form>
  <div class="error-msg" id="errMsg"></div>
</div>
<script>
document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const err = document.getElementById('errMsg');
  const uname = document.getElementById('uname').value;
  const pwd = document.getElementById('pwd').value;
  btn.disabled = true;
  err.style.display = 'none';
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: uname, password: pwd })
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      sessionStorage.setItem('stlgen_ws_token', data.token || '');
      window.location.href = '/';
    } else {
      err.textContent = data.error || 'Invalid credentials';
      err.style.display = 'block';
    }
  } catch (ex) {
    err.textContent = 'Connection failed: ' + ex.message;
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
});
</script>
</body>
</html>
"""


def handle_auth_login(handler):
    """POST /api/auth/login -- Validate username/password and issue session."""
    if AUTH_MGR is None:
        _send_json(handler, 200, {"ok": True, "token": ""})
        return

    body = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        _send_json(handler, 400, {"error": "Invalid JSON"})
        return

    ip = _get_client_ip(handler)

    # Check IP ban
    banned, reason = AUTH_MGR.check_ip_banned(ip)
    if banned:
        _send_json(handler, 403, {"ok": False, "error": reason or "IP banned"})
        return

    username = data.get("username", "")
    password = data.get("password", "")

    ok, user_info = AUTH_MGR.authenticate(username, password)
    if not ok:
        AUTH_MGR.record_login_failure(ip)
        AUTH_MGR.log(f"LOGIN_FAILED: user={username!r} ip={ip}")
        _send_json(handler, 401, {"ok": False, "error": "Invalid username or password"})
        return

    AUTH_MGR.record_login_success(ip)
    token = AUTH_MGR.create_session(username, user_info["role"], ip)
    AUTH_MGR.log(f"LOGIN_SUCCESS: user={username} ip={ip}")

    # Send response with Set-Cookie
    resp_body = json.dumps({"ok": True, "token": token, "username": username, "role": user_info["role"]}).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(resp_body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    cookie_name = _auth_cookie_name(handler)
    cookie = http.cookies.SimpleCookie()
    cookie[cookie_name] = token
    cookie[cookie_name]["path"] = "/"
    cookie[cookie_name]["httponly"] = True
    cookie[cookie_name]["samesite"] = "Lax"
    cookie[cookie_name]["max-age"] = str(12 * 3600)
    handler.send_header("Set-Cookie", cookie[cookie_name].OutputString())
    cookies = _auth_cookies(handler)
    legacy = cookies.get(AUTH_COOKIE_NAME) if cookies is not None else None
    if legacy is not None and AUTH_MGR.validate_session(legacy.value):
        # Remove only our old browser cookie so it cannot resurrect after logout.
        # Another instance's legacy session must remain untouched.
        _expire_auth_cookie(handler, AUTH_COOKIE_NAME)
    handler.end_headers()
    handler.wfile.write(resp_body)


def handle_auth_check(handler):
    """GET /api/auth/check -- Check if the current session is valid."""
    if AUTH_MGR is None:
        _send_json(handler, 200, {"authenticated": True, "authRequired": False})
        return
    # Internal headless browser worker runs in the same process and cannot carry
    # a session cookie; report authenticated for loopback so the frontend auth
    # gate lets it into the UI.
    if _get_client_ip(handler) in ("127.0.0.1", "::1"):
        _send_json(handler, 200, {
            "authenticated": True,
            "authRequired": True,
            "username": "__loopback__",
            "role": "admin",
        })
        return
    session = _get_session(handler)
    resp = {
        "authenticated": session is not None,
        "authRequired": True,
    }
    if session:
        resp["username"] = session["username"]
        resp["role"] = session["role"]
    _send_json(handler, 200, resp)


def handle_auth_logout(handler):
    """POST /api/auth/logout -- Invalidate session, clear cookie."""
    session = _get_session(handler)
    if session and AUTH_MGR:
        AUTH_MGR.delete_session(session["_token"])
        AUTH_MGR.log(f"LOGOUT: user={session['username']}")
    # Clear cookie
    resp_body = json.dumps({"ok": True}).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(resp_body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    _expire_auth_cookie(handler, _auth_cookie_name(handler))
    if session and session["_cookie_name"] == AUTH_COOKIE_NAME:
        _expire_auth_cookie(handler, AUTH_COOKIE_NAME)
    handler.end_headers()
    handler.wfile.write(resp_body)


def _serve_login_page(handler):
    """Serve the login HTML page."""
    body = _LOGIN_PAGE_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _redirect_to_login(handler):
    """Send 302 redirect to login page."""
    handler.send_response(302)
    handler.send_header("Location", "/login")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


# ---------------------------------------------------------------------------
# Admin API handlers
# ---------------------------------------------------------------------------

def handle_admin_users_get(handler):
    """GET /api/admin/users -- List all users."""
    _send_json(handler, 200, {"users": AUTH_MGR.list_users()})


def handle_admin_users_post(handler):
    """POST /api/admin/users -- Create a new user."""
    body = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        _send_json(handler, 400, {"error": "Invalid JSON"})
        return
    username = data.get("username", "")
    password = data.get("password", "")
    role = data.get("role", "viewer")
    if not username or not password:
        _send_json(handler, 400, {"error": "username and password are required"})
        return
    ok, msg = AUTH_MGR.create_user(username, password, role)
    if ok:
        _send_json(handler, 201, {"ok": True, "message": msg})
    else:
        _send_json(handler, 400, {"ok": False, "error": msg})


def handle_admin_user_delete(handler, username):
    """DELETE /api/admin/users/<username> -- Delete a user."""
    session = _get_session(handler)
    acting = session["username"] if session else "unknown"
    ok, msg = AUTH_MGR.delete_user(username, acting)
    if ok:
        _send_json(handler, 200, {"ok": True, "message": msg})
    else:
        _send_json(handler, 400, {"ok": False, "error": msg})


def handle_admin_user_password(handler, username):
    """POST /api/admin/users/<username>/password -- Change user password."""
    body = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        _send_json(handler, 400, {"error": "Invalid JSON"})
        return
    new_password = data.get("password", "")
    if not new_password:
        _send_json(handler, 400, {"error": "password is required"})
        return
    ok, msg = AUTH_MGR.change_password(username, new_password)
    if ok:
        _send_json(handler, 200, {"ok": True, "message": msg})
    else:
        _send_json(handler, 400, {"ok": False, "error": msg})


def handle_admin_user_role(handler, username):
    """POST /api/admin/users/<username>/role -- Change user role."""
    body = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        _send_json(handler, 400, {"error": "Invalid JSON"})
        return
    session = _get_session(handler)
    acting = session["username"] if session else "unknown"
    new_role = data.get("role", "")
    if not new_role:
        _send_json(handler, 400, {"error": "role is required"})
        return
    ok, msg = AUTH_MGR.change_role(username, new_role, acting)
    if ok:
        _send_json(handler, 200, {"ok": True, "message": msg})
    else:
        _send_json(handler, 400, {"ok": False, "error": msg})


def handle_admin_sessions_get(handler):
    """GET /api/admin/sessions -- List all active sessions."""
    _send_json(handler, 200, {"sessions": AUTH_MGR.list_sessions()})


def handle_admin_session_delete(handler, prefix):
    """DELETE /api/admin/sessions/<prefix> -- Delete session by token prefix."""
    ok = AUTH_MGR.delete_session_by_prefix(prefix)
    if ok:
        _send_json(handler, 200, {"ok": True, "message": "Session revoked"})
    else:
        _send_json(handler, 404, {"ok": False, "error": "Session not found"})


def handle_admin_bans_get(handler):
    """GET /api/admin/bans -- List all IP bans."""
    _send_json(handler, 200, {"bans": AUTH_MGR.list_bans()})


def handle_admin_ban_delete(handler, ip):
    """DELETE /api/admin/bans/<ip> -- Unban an IP."""
    ok = AUTH_MGR.unban_ip(ip)
    if ok:
        _send_json(handler, 200, {"ok": True, "message": "IP unbanned"})
    else:
        _send_json(handler, 404, {"ok": False, "error": "IP not found in bans"})


# ── Upgrade / Restart endpoints ──────────────────────────────────────────

# REPO_ROOT for git operations: dcdtuni root (2 levels above src/server/server.py)
_UPGRADE_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUBMODULE_DIR = _UPGRADE_REPO_ROOT / "src" / "frontend"


def _run_git(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Run a git command, return (returncode, combined stdout+stderr)."""
    r = subprocess.run(
        ["git"] + args,
        cwd=str(cwd or _UPGRADE_REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def handle_admin_upgrade(handler):
    """POST /api/admin/upgrade -- Pull latest code from git."""
    log_lines: list[str] = []

    def log(msg: str):
        log_lines.append(msg)
        print(f"[upgrade] {msg}")

    try:
        # 1. Fetch
        log("$ git fetch origin")
        rc, out = _run_git(["fetch", "origin"])
        if out:
            log(out)
        if rc != 0:
            _send_json(handler, 500, {"ok": False, "status": "fetch_failed", "log": log_lines})
            return

        # 2. Check if up to date
        rc_local, local_sha = _run_git(["rev-parse", "HEAD"])
        rc_remote, remote_sha = _run_git(["rev-parse", "@{u}"])
        if rc_local == 0 and rc_remote == 0 and local_sha == remote_sha:
            log("Already up to date.")
            _send_json(handler, 200, {"ok": True, "status": "already_up_to_date", "log": log_lines})
            return

        # 3. Try pull
        log("$ git pull --recurse-submodules")
        rc, out = _run_git(["pull", "--recurse-submodules"])
        if out:
            log(out)

        if rc == 0:
            log("Pull succeeded.")
            _send_json(handler, 200, {"ok": True, "status": "updated", "restart": True, "log": log_lines})
            return

        # 4. Conflict — collect conflicting files
        log("Pull failed — checking for conflicts...")
        _run_git(["merge", "--abort"])

        _, diff_out = _run_git(["diff", "--name-only", "HEAD", "@{u}"])
        conflict_files = [f for f in diff_out.splitlines() if f.strip()]
        log(f"Conflicting files: {', '.join(conflict_files) if conflict_files else '(unknown)'}")

        _send_json(handler, 200, {
            "ok": False, "status": "conflict",
            "files": conflict_files, "log": log_lines,
        })

    except subprocess.TimeoutExpired:
        log("Git command timed out (120s)")
        _send_json(handler, 500, {"ok": False, "status": "timeout", "log": log_lines})
    except Exception as exc:
        log(f"Unexpected error: {exc}")
        _send_json(handler, 500, {"ok": False, "status": "error", "log": log_lines})


def handle_admin_upgrade_force(handler):
    """POST /api/admin/upgrade/force -- Stash local changes, then pull."""
    log_lines: list[str] = []

    def log(msg: str):
        log_lines.append(msg)
        print(f"[upgrade-force] {msg}")

    try:
        from datetime import datetime
        stash_name = f"upgrade-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"

        # 1. Stash main repo
        log(f"$ git stash push -m '{stash_name}'")
        rc, out = _run_git(["stash", "push", "-m", stash_name])
        if out:
            log(out)

        # 2. Stash submodule (if dirty)
        log(f"$ cd frontend && git stash push -m '{stash_name}'")
        rc_sub, out_sub = _run_git(["stash", "push", "-m", stash_name], cwd=_SUBMODULE_DIR)
        if out_sub:
            log(out_sub)

        # 3. Pull
        log("$ git pull --recurse-submodules")
        rc, out = _run_git(["pull", "--recurse-submodules"])
        if out:
            log(out)

        if rc == 0:
            log("Pull succeeded after stash.")
            _send_json(handler, 200, {
                "ok": True, "status": "updated", "stashed": True,
                "restart": True, "log": log_lines,
            })
        else:
            log("Pull still failed after stash — manual intervention required.")
            _send_json(handler, 500, {"ok": False, "status": "error", "log": log_lines})

    except Exception as exc:
        log(f"Unexpected error: {exc}")
        _send_json(handler, 500, {"ok": False, "status": "error", "log": log_lines})


def handle_admin_restart(handler):
    """POST /api/admin/restart -- Restart the server process."""
    # Build the command to restart with same arguments
    server_script = str(Path(__file__).resolve())
    cmd_parts = [sys.executable, server_script]

    # Preserve current port/ws-port from the running server
    # Parse from sys.argv since we have access to the original args
    import shlex
    argv = sys.argv[1:]  # original CLI args passed to server.py
    cmd_parts.extend(argv)

    cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)
    restart_cmd = f"sleep 2 && exec {cmd_str}"

    print(f"[restart] Spawning new server: {restart_cmd}")
    _send_json(handler, 200, {"ok": True, "message": "Restarting..."})

    # Spawn new server process with delay, then exit
    subprocess.Popen(restart_cmd, shell=True, start_new_session=True,
                     cwd=str(_UPGRADE_REPO_ROOT))

    # Give time for the response to be sent
    import time
    time.sleep(0.5)
    print("[restart] Exiting current server process.")
    os._exit(0)


# Set via --case-dir at startup (can also be changed at runtime via /api/set-case-dir)
CASE_DIR: Path | None = None
CASE_DIR_LOCK = threading.Lock()
CASE_DIR_VERSION = 0

# Check dcdtuni availability once at startup
try:
    import backend  # noqa: F401
    DCTWIN_AVAILABLE = True
    DCTWIN_VERSION = getattr(backend, "__version__", "unknown")
except ImportError as _e:
    DCTWIN_AVAILABLE = False
    DCTWIN_VERSION = str(_e)


# ---------------------------------------------------------------------------
# .cfdrun filename sanitization + active-manifest write helpers
#
# Threat model: the filename is chosen client-side (browser JS or a curl call)
# and flows into subprocess argv below. A malicious or malformed filename could
# break the run command, or — before 2026-04-11 — be interpolated into a bash
# string and execute arbitrary commands on the server. These helpers close that
# path and also enforce "one active .cfdrun per case" so liquid endpoints that
# glob model/*.cfdrun cannot silently grab a stale manifest.
# ---------------------------------------------------------------------------

# Allow only safe filename characters. Empty → fallback applied below.
_CFDRUN_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _sanitize_cfdrun_filename(raw: str | None) -> str:
    """Return a safe `<name>.cfdrun` filename.

    - Strips any path components (defends against `../../etc/passwd.cfdrun`).
    - Replaces every disallowed character with `_`, so a filename like
      `foo; rm -rf ~.cfdproj` becomes `foo__rm_-rf____cfdproj.cfdrun` — ugly
      but safe to pass as an argv element and safe to write to disk.
    - Guarantees a non-empty stem and a `.cfdrun` suffix.

    This is the ONLY function that decides what a `.cfdrun` filename may look
    like on this server. All write-side handlers route through it.
    """
    base = Path((raw or "")).name  # drop any directory components
    if not base:
        base = "project.cfdrun"
    # Split off suffix if user passed one, sanitize stem, rebuild.
    if base.lower().endswith(".cfdrun"):
        stem = base[: -len(".cfdrun")]
    else:
        stem = base
    # Replace every disallowed char with `_`. Cheap, loud, and reversible
    # enough that a human debugging can still see the original shape.
    safe_stem = "".join(c if _CFDRUN_NAME_RE.match(c) else "_" for c in stem)
    # Collapse runs of underscores and strip leading dots (hidden-file safety).
    safe_stem = re.sub(r"_+", "_", safe_stem).strip("._") or "project"
    return f"{safe_stem}.cfdrun"


def _write_active_cfdrun(
    model_dir: Path,
    raw_filename: str | None,
    content: str,
    *,
    published_model_dir: Path | None = None,
) -> Path:
    """Persist `content` as the single active .cfdrun manifest in `model_dir`.

    Centralizes three security/consistency invariants that Codex called out in
    the 2026-04-11 adversarial review:

    1. Filename is sanitized via `_sanitize_cfdrun_filename` before touching
       the filesystem or any subprocess argv.
    2. After writing the new manifest, every *other* `*.cfdrun` sibling in the
       same dir is deleted. This guarantees that downstream readers (the
       liquid endpoints in particular) see exactly one manifest and cannot
       silently fall back to a stale rename.
    3. Legacy `bridge_bc.json` is cleaned up (it is in-memory only after the
       2026-04-11 refactor).

    Returns the resolved path of the written file.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    filename = _sanitize_cfdrun_filename(raw_filename)
    cfdrun_path = model_dir / filename
    # Uploads first write to a staging directory. V2 artifacts must still
    # identify the published case, never the temporary staging path.
    published_model_dir = published_model_dir or model_dir
    if cfdrun_path.is_symlink():
        cfdrun_path.unlink()

    # P3 T6: for V2 manifests, fill artifacts.* with the resolved absolute paths
    # plus an empty logs skeleton, then rewrite. The browser cannot know the
    # server-side case layout, so artifacts is server-filled here. Non-V2 or
    # unparseable payloads are written through unchanged (validation happens in
    # the runner / cfdrun_reader, which raises the A12 "re-export" error).
    try:
        manifest = json.loads(content)
    except (ValueError, TypeError):
        manifest = None
    if isinstance(manifest, dict) and manifest.get("schemaVersion") in (2, 3, 4):
        case_dir = published_model_dir.parent.resolve()
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            artifacts = {}
        artifacts["caseDir"] = str(case_dir)
        artifacts["modelDir"] = str(published_model_dir.resolve())
        artifacts["triSurfaceDir"] = str(
            (case_dir / "Mesh" / "constant" / "triSurface").resolve()
        )
        artifacts["openfoamCaseDir"] = str((case_dir / "Mesh").resolve())
        if not isinstance(artifacts.get("logs"), dict):
            artifacts["logs"] = {}
        manifest["artifacts"] = artifacts
        content = json.dumps(manifest, ensure_ascii=False, indent=2)

    cfdrun_path.write_text(content, encoding="utf-8")

    # Sweep sibling .cfdrun files so liquid endpoints can safely glob.
    for sibling in sorted(model_dir.glob("*.cfdrun")):
        if sibling != cfdrun_path:
            sibling.unlink()
            print(f"[server] Removed stale sibling manifest: {sibling.name}")

    # Legacy bridge_bc.json is self-healing on rollback (main regenerates it),
    # so it is safe to remove here.
    stale_bridge = model_dir / "bridge_bc.json"
    if stale_bridge.exists():
        try:
            stale_bridge.unlink()
            print("[server] Removed legacy bridge_bc.json (now in-memory)")
        except OSError as exc:
            print(f"[server] WARNING: could not remove bridge_bc.json: {exc}")

    return cfdrun_path


def _decode_stl_uploads(stl_files: dict | None) -> dict[str, bytes]:
    """Validate the complete batch before any existing case input is moved."""
    if stl_files is None:
        return {}
    if not isinstance(stl_files, dict):
        raise ValueError("stlFiles must be an object")
    decoded = {}
    for filename, encoded in stl_files.items():
        if (not isinstance(filename, str) or not filename or
                Path(filename).name != filename or filename in (".", "..") or
                not filename.lower().endswith(".stl")):
            raise ValueError(f"Invalid STL filename: {filename!r}")
        if not isinstance(encoded, str):
            raise ValueError(f"STL content must be base64 text: {filename}")
        try:
            decoded[filename] = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Invalid base64 STL content: {filename}") from exc
    return decoded


def _publish_case_inputs(
    case_dir: Path,
    cfdrun_filename: str | None,
    cfdrun_json: str | None,
    stl_files: dict | None,
    *,
    require_manifest: bool = True,
    replace_stls: bool = False,
) -> tuple[Path | None, str | None]:
    """Publish an entire uploaded input batch under the cross-process case lock.

    Staging and validation happen before moving existing data. The two affected
    directories are swapped while the lock is held; if any rename fails, the
    previous directories are restored before an error is returned. Readers
    participating in the case lock never observe a partial batch.
    """
    case_dir = Path(case_dir).expanduser().resolve()
    with case_execution_lock(case_dir):
        decoded_stls = _decode_stl_uploads(stl_files)
        replace_stls = replace_stls or bool(decoded_stls)
        if cfdrun_json is not None and not isinstance(cfdrun_json, str):
            raise ValueError("cfdrunJson must be a JSON string")
        model_dir = case_dir / "model"
        tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
        existing = sorted(model_dir.glob("*.cfdrun")) if model_dir.is_dir() else []
        if not cfdrun_json and require_manifest and not existing:
            raise ValueError("No cfdrun content provided or found in case/model directory")
        active_path = (model_dir / _sanitize_cfdrun_filename(cfdrun_filename)
                       if cfdrun_json else (existing[0] if existing else None))
        if not cfdrun_json and not replace_stls:
            return active_path, (hashlib.sha256(active_path.read_bytes()).hexdigest()
                                 if active_path is not None else None)

        scratch = Path(tempfile.mkdtemp(prefix=".dcdtuni-upload-", dir=case_dir))
        stage = scratch / "stage"
        backup = scratch / "backup"
        stage.mkdir()
        backup.mkdir()
        changes: list[tuple[Path, Path, Path]] = []
        moved_old: list[tuple[Path, Path]] = []
        moved_new: list[Path] = []
        restored = False
        try:
            if cfdrun_json:
                stage_model = stage / "model"
                if model_dir.is_dir():
                    shutil.copytree(model_dir, stage_model, symlinks=True)
                else:
                    stage_model.mkdir()
                _write_active_cfdrun(
                    stage_model, cfdrun_filename, cfdrun_json,
                    published_model_dir=model_dir,
                )
                changes.append((model_dir, stage_model, backup / "model"))

            if replace_stls:
                stage_tri = stage / "triSurface"
                if tri_dir.is_dir():
                    shutil.copytree(tri_dir, stage_tri, symlinks=True)
                else:
                    stage_tri.mkdir()
                for path in stage_tri.iterdir():
                    if (path.is_file() or path.is_symlink()) and path.suffix.lower() in (".stl", ".emesh"):
                        path.unlink()
                for filename, data in decoded_stls.items():
                    (stage_tri / filename).write_bytes(data)
                changes.append((tri_dir, stage_tri, backup / "triSurface"))

            sha_source = ((stage / "model" / active_path.name) if cfdrun_json else active_path)
            sha = hashlib.sha256(sha_source.read_bytes()).hexdigest() if sha_source else None

            for live, staged, prior in changes:
                live.parent.mkdir(parents=True, exist_ok=True)
                if live.exists():
                    live.rename(prior)
                    moved_old.append((prior, live))
                staged.rename(live)
                moved_new.append(live)
            restored = True
        except Exception:
            try:
                for live in reversed(moved_new):
                    shutil.rmtree(live)
                for prior, live in reversed(moved_old):
                    prior.rename(live)
                restored = True
            finally:
                if restored:
                    shutil.rmtree(scratch, ignore_errors=True)
            raise
        finally:
            if restored:
                shutil.rmtree(scratch, ignore_errors=True)

        return active_path, sha


# Global job registry: job_id -> {lines, done, returncode}
JOBS: dict = {}
JOBS_LOCK = threading.Lock()


class _JobCancelledError(Exception):
    """A registered worker was cancelled before starting its next operation."""


def _new_job_state(lines: list[str], **fields) -> dict:
    return {
        "lines": lines, "done": False, "returncode": None, "proc": None,
        "cancelRequested": False, "cancelled": False,
        # Serializes tree termination with the worker's final cleanup. The
        # abort handler never owns the terminal state or a case/controller lock.
        "_stop_lock": threading.Lock(), "_stop_started": False,
        **fields,
    }


def _raise_if_job_cancelled(job_id: str) -> None:
    with JOBS_LOCK:
        if JOBS[job_id].get("cancelRequested"):
            raise _JobCancelledError()


def _spawn_job_process(job_id: str, command, **kwargs):
    # Cancellation and process publication are one transaction. An abort
    # accepted while browser cleanup/admission is pending cannot late-spawn.
    with JOBS_LOCK:
        job = JOBS[job_id]
        if job.get("cancelRequested"):
            raise _JobCancelledError()
        proc = subprocess.Popen(command, **kwargs)
        job["proc"] = proc
        return proc


def _finish_job(job_id: str, returncode: int, *, quality=None,
                cancelled_receipt: Path | None = None) -> None:
    """Publish terminal state only from the worker, after its own cleanup."""
    with JOBS_LOCK:
        job = JOBS[job_id]
        stop_lock = job["_stop_lock"]
    # This final fence also covers an abort arriving after stdout/receipt work.
    # The handler only try-acquires this lock while holding JOBS_LOCK.
    with stop_lock:
        with JOBS_LOCK:
            cancelled = job.get("cancelRequested", False)
            proc = job.get("proc")
            if not cancelled:
                if quality is not None:
                    job.update(quality)
                job.update(returncode=returncode, done=True)
                return
        if proc is not None:
            while _terminate_job_tree(proc) is False:
                time.sleep(0.1)
            proc.wait()
        if cancelled_receipt is not None:
            try:
                cancelled_receipt.unlink(missing_ok=True)
            except OSError as exc:
                with JOBS_LOCK:
                    job["lines"].append(f"[server] Cancelled receipt cleanup failed: {exc}")
        with JOBS_LOCK:
            job.pop("meshQualityReceipt", None)
            job.update(returncode=-15, cancelled=True, checkMeshOk=False, done=True)


def _join_job_cleanup(job_id: str, proc) -> None:
    if proc is None:
        return
    with JOBS_LOCK:
        job = JOBS[job_id]
        stop_lock = job["_stop_lock"]
    # A pipe may reach EOF while the abort handler is still stopping a
    # separately-sessioned MPI group. Wait for that cleanup before done.
    with stop_lock:
        with JOBS_LOCK:
            cancelled = job.get("cancelRequested", False)
        if cancelled:
            while _terminate_job_tree(proc) is False:
                time.sleep(0.1)
        elif proc.returncode is None:
            _terminate_job_tree(proc)
        proc.wait()


_LATEST_SOLVE_PREFIX: str = ""
# The run directory of the solve that is running or just finished. Pinned from
# the runner's own "Created run directory:" line, because the prefix above is
# only minute-resolution and a run dir named after the next minute boundary
# (click 11:45:56, dir 11:46:02) never matches it.
_LATEST_SOLVE_RUN_DIR = None
_LATEST_SOLVE_STARTED = None
_RUN_DIR_LINE_RE = re.compile(r"Created run directory:\s*(\S.*?)\s*$")


def _capture_solve_run_dir(line: str):
    """Pin ``_LATEST_SOLVE_RUN_DIR`` when a job line names the run directory."""
    global _LATEST_SOLVE_RUN_DIR
    m = _RUN_DIR_LINE_RE.search(line or "")
    if not m:
        return None
    from pathlib import Path as _P
    run_dir = _P(m.group(1))
    _LATEST_SOLVE_RUN_DIR = run_dir
    return run_dir

_CHECK_MESH_FAILED_RE = re.compile(r"Failed\s+(\d+)\s+mesh checks?\.", re.IGNORECASE)


def _summarize_check_mesh_result(lines: list[str], returncode: int | None) -> dict:
    """Interpret checkMesh output into structured warnings.

    Returns checkMeshOk (legacy compat), failedChecks, and a new
    ``meshWarnings`` list with severity-leveled warnings for the
    frontend dialog.  ``meshReady`` is always True when checkMesh
    runs — the user decides whether to proceed via the dialog.
    """
    from backend.aircooling.runner.mesh_quality import parse_checkmesh_warnings

    failed_checks: int | None = None
    explicit_failure = False
    explicit_success = False

    for line in lines:
        match = _CHECK_MESH_FAILED_RE.search(line)
        if match:
            failed_checks = int(match.group(1))
            explicit_failure = failed_checks > 0
        if "Mesh OK." in line:
            explicit_success = True
        if "[server] checkMesh failed:" in line:
            explicit_failure = True

    has_verdict = explicit_success or failed_checks is not None
    ok = returncode == 0 and has_verdict and not explicit_failure

    full_output = "\n".join(lines)
    warnings = parse_checkmesh_warnings(full_output)
    if not ok and not warnings:
        command_failed = returncode not in (None, 0) or "[server] checkMesh failed:" in full_output
        code = ("failed_checks" if failed_checks and failed_checks > 0
                else "checkmesh_command_failed" if command_failed
                else "incomplete_checkmesh")
        warnings = [{
            "level": 2,
            "code": code,
            "message": (f"checkMesh failed {failed_checks} quality check(s)."
                        if code == "failed_checks" else
                        "checkMesh command failed before a usable quality report."
                        if command_failed else "checkMesh did not produce a complete quality verdict."),
        }]

    return {
        "checkMeshOk": ok,
        "failedChecks": failed_checks,
        "hasExplicitSuccess": explicit_success,
        "meshWarnings": warnings,
    }



def _summarize_solve_mesh_quality_rejection(lines: list[str], returncode: int | None) -> dict | None:
    """Carry an explicit runner quality refusal into the solve job's UI verdict.

    A solver, boundary-condition or input error alone says nothing about mesh
    quality. This helper can report a refusal; it never authorizes a mesh.
    """
    if returncode in (None, 0):
        return None
    refusals = (
        "RuntimeError: air solve requires accepted strict mesh quality:",
        "RuntimeError: source-aware air solve requires accepted strict mesh quality:",
    )
    if not any(line.strip().startswith(refusals) for line in lines):
        return None
    result = _summarize_check_mesh_result(lines, returncode)
    result["checkMeshOk"] = False
    return result

def _summarize_mesh_run_quality(lines: list[str], returncode: int | None) -> dict:
    """Read the final checkMesh pass from a mesh job, after any repair/recheck.

    A mesher may print its own intermediate ``Mesh OK`` or failed checks.
    Only the runner's final quality step describes the mesh it delivers. A
    completed OpenFOAM command can exit zero despite ``Failed N mesh checks``.
    """
    steps = [i for i, line in enumerate(lines) if "checkMesh (final quality check)" in line]
    if not steps:
        return {
            "checkMeshOk": False,
            "failedChecks": None,
            "meshWarnings": [{"level": 2, "code": "missing_final_checkmesh",
                              "message": "Mesh job did not run a final checkMesh quality check."}],
        }
    quality_lines = lines[steps[-1] + 1:]
    verdicts = [i for i, line in enumerate(quality_lines)
                if _CHECK_MESH_FAILED_RE.search(line) or "Mesh OK." in line]
    if not verdicts:
        return {
            "checkMeshOk": False,
            "failedChecks": None,
            "meshWarnings": [{"level": 2, "code": "incomplete_checkmesh",
                              "message": "Final checkMesh did not produce a quality verdict."}],
        }

    last = verdicts[-1]
    previous = verdicts[-2] + 1 if len(verdicts) > 1 else 0
    report = quality_lines[previous:last + 1]
    starts = [i for i, line in enumerate(report) if "Checking topology..." in line]
    if not starts:
        starts = [i for i, line in enumerate(report) if "Checking geometry..." in line]
    if starts:
        report = report[starts[-1]:]
    summary = _summarize_check_mesh_result(report, returncode)
    if summary["failedChecks"] is None and summary["hasExplicitSuccess"]:
        summary["failedChecks"] = 0
    if not summary["checkMeshOk"] and not summary["meshWarnings"]:
        summary["meshWarnings"] = [{
            "level": 2,
            "code": "failed_checks",
            "message": f"Final checkMesh failed {summary['failedChecks']} quality check(s).",
        }]
    return summary

# ---------------------------------------------------------------------------
# Persistent headless browser session (lazy-init on first /api/browser/* call)
# ---------------------------------------------------------------------------
# Playwright sync API is thread-bound: all calls must happen on the thread
# that created the browser.  We run a dedicated "browser worker" thread with
# a command queue; HTTP handler threads enqueue (fn, args) and block on
# an Event until the worker thread executes the call and posts the result.
# ---------------------------------------------------------------------------

import queue as _queue

_browser_cmd_q: _queue.Queue | None = None
_browser_ready = threading.Event()
_browser_console_logs: list[str] = []
_browser_errors: list[str] = []
_browser_init_lock = threading.Lock()


class _BrowserWorkerState:
    """One generation owns its queue, events and Playwright resources.

    admitted counts queued AND executing calls until the owning thread's
    finally completes, including calls whose HTTP waiter has timed out.
    """
    def __init__(self):
        self.queue = _queue.Queue()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.admitted = 0
        self.sequences = 0
        self.active = None
        self.closing = False
        self.software = os.environ.get("DCDTUNI_BROWSER_SOFTWARE") == "1"
        self.headless = _BROWSER_HEADLESS
        self.start_error = None
        self.cleanup_error = None

_browser_state: _BrowserWorkerState | None = None
_browser_thread: threading.Thread | None = None

def _finish_browser_worker(state: _BrowserWorkerState, cleanup_errors: list[str]) -> None:
    """Fail this generation's waiters; never clear a replacement worker."""
    global _browser_state, _browser_cmd_q, _browser_ready, _browser_thread
    state.cleanup_error = "; ".join(cleanup_errors) if cleanup_errors else None
    while True:
        try:
            cmd = state.queue.get_nowait()
        except _queue.Empty:
            break
        if cmd is not None:
            _, _, event, holder = cmd
            holder["error"] = state.start_error or "Browser worker closed before the operation completed"
            event.set()
    with _browser_init_lock:
        state.ready.clear()
        state.admitted = 0
        state.active = None
        if _browser_state is state:
            if not state.cleanup_error:
                _browser_state = None
                _browser_cmd_q = None
                _browser_thread = None
            else:
                # Uncertain closure fences reuse, including a lazy restart.
                state.closing = True
        state.stopped.set()

def _close_browser(*, timeout: float = 30.0, software_only: bool = False,
                   idle_only: bool = True) -> dict:
    """Atomically queue an idle close; wait outside the admission lock.

    Never queue idle-only closure behind page.evaluate: that JS can itself
    await a native CFD HTTP request handled by the thread calling us.
    """
    with _browser_init_lock:
        state = _browser_state
        if state is None:
            return {"ok": True, "closed": True, "status": "closed"}
        if software_only and not state.software:
            return {"ok": True, "closed": False, "status": "not_applicable"}
        if idle_only and (state.admitted or state.sequences or
                          (not state.ready.is_set() and not state.closing)):
            # A shutdown sentinel can already be queued behind JS awaiting
            # this HTTP thread. Busy still means skip, never wait for itself.
            return {"ok": True, "closed": False, "status": "active",
                    "pending": state.admitted + state.sequences,
                    "operation": state.active or ("export_sequence" if state.sequences else "starting_or_queued")}
        if not state.closing:
            state.closing = True
            state.queue.put(None)
    deadline = time.monotonic() + timeout
    if not state.stopped.wait(timeout=timeout):
        return {"ok": False, "closed": False, "status": "closing",
                "error": "Browser resource closure is still in progress"}
    if state.thread is not None and state.thread is not threading.current_thread():
        state.thread.join(timeout=max(0, deadline - time.monotonic()))
        if state.thread.is_alive():
            return {"ok": False, "closed": False, "status": "closing",
                    "error": "Browser worker thread is still stopping"}
    if state.cleanup_error:
        return {"ok": False, "closed": False, "status": "failed", "error": state.cleanup_error}
    return {"ok": True, "closed": True, "status": "closed"}

@contextmanager
def _browser_sequence():
    """Keep a multi-command geometry export intact between queued calls."""
    with _browser_init_lock:
        state = _browser_state
        if state is None or state.closing or not state.ready.is_set():
            raise RuntimeError("Browser worker is not ready or is closing")
        state.sequences += 1
    try:
        yield
    finally:
        with _browser_init_lock:
            state.sequences -= 1


def _release_idle_software_browser() -> dict:
    """Release this server's software worker (headed or headless) before CFD."""
    result = _close_browser(software_only=True)
    if result["status"] != "not_applicable":
        print(f"[browser-session] native CFD browser release: {result['status']}"
              f" (operation={result.get('operation', 'none')})", flush=True)
    return result


# The flags that put WebGL on the discrete GPU.
#
# ANGLE over Vulkan, on the NVIDIA Vulkan ICD (/etc/vulkan/icd.d/nvidia_icd.json).
# `--use-gl=egl` is the route that does NOT work in a window — it is what the
# install notes meant by "a headed window falls back to llvmpipe". Measured on
# this host, ANGLE/Vulkan reaches "ANGLE (NVIDIA, Vulkan 1.4.312 (RTX 4090))" in
# BOTH a headless=new browser and a real window on Xvfb, so running headed costs
# nothing in rendering.
_BROWSER_GPU_ARGS = [
    "--use-gl=angle",
    "--use-angle=vulkan",
    "--enable-features=Vulkan",
    "--enable-gpu",
    "--ignore-gpu-blocklist",
    "--disable-software-rasterizer",
    "--disable-gpu-sandbox",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# The regular worker uses the NVIDIA Vulkan device for visible engineering
# screenshots. A bounded export-only run can opt into software WebGL without
# competing for a physical GPU on a shared server.
_BROWSER_SOFTWARE_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


def _browser_launch_args() -> list[str]:
    if os.environ.get("DCDTUNI_BROWSER_SOFTWARE") == "1":
        return _BROWSER_SOFTWARE_ARGS
    return _BROWSER_GPU_ARGS

_BROWSER_DISPLAY = os.environ.get("DCDTUNI_BROWSER_DISPLAY", ":99")

def _browser_headless() -> bool:
    """Software automation needs no X display; an explicit setting wins."""
    explicit = os.environ.get("DCDTUNI_BROWSER_HEADLESS")
    if explicit is not None:
        return explicit == "1"
    return os.environ.get("DCDTUNI_BROWSER_SOFTWARE") == "1"

# Hardware previews retain their headed default. Software automation defaults
# to headless so an unused or stale Xvfb cannot prevent geometry export.
# HEADLESS=0 explicitly requests a window; HEADLESS=1 works with either renderer.
_BROWSER_HEADLESS = _browser_headless()


def _ensure_x_display() -> str | None:
    """Return a DISPLAY the head browser can open a window on, starting Xvfb if needed.

    Returns None when no display could be provided, which is the signal to fall
    back to headless rather than to fail: a working headless browser beats a
    dead head one, and the caller says so in the log.
    """
    display = _BROWSER_DISPLAY

    def _alive() -> bool:
        try:
            return subprocess.run(["xdpyinfo"], env={**os.environ, "DISPLAY": display},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=5).returncode == 0
        except Exception:
            return False

    if _alive():
        return display
    if not shutil.which("Xvfb"):
        print(f"[browser-session] no Xvfb binary and {display} is down", flush=True)
        return None
    print(f"[browser-session] starting Xvfb on {display}", flush=True)
    try:
        # Left running on purpose. It is shared with the Omniverse demo's
        # head-verify scripts, costs nothing idle, and this repo's rules allow
        # no way to stop a process except by port — which an X server has none of.
        subprocess.Popen(["Xvfb", display, "-screen", "0", "1920x1080x24"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:
        print(f"[browser-session] Xvfb failed to start: {e}", flush=True)
        return None
    for _ in range(20):
        time.sleep(0.5)
        if _alive():
            return display
    print(f"[browser-session] Xvfb did not come up on {display}", flush=True)
    return None


def _browser_load_project_script(project_text: str) -> str:
    """Use the interactive loader shared by React and the archived editor."""
    script = """(async () => {
        try {
            const raw = JSON.parse(__PROJECT_TEXT__);
            const react = window.__dcdtuni;
            if (typeof window._applyProjectData !== 'function') {
                throw new Error('Interactive project loader is unavailable');
            }
            if (react) {
                if (typeof react.getProject !== 'function' ||
                    typeof react.allModels !== 'function' ||
                    typeof react.revision !== 'function' ||
                    typeof react.buildGeometryData !== 'function') {
                    throw new Error('React project automation contract is incomplete');
                }
                const before = react.getProject();
                const beforeRevision = react.revision();
                const applied = window._applyProjectData(raw, 'headless-load',
                    { clearRemotePath: true });
                const loaded = react.getProject();
                const loadedRevision = react.revision();
                if (applied !== true || !loaded || loaded === before ||
                    loadedRevision <= beforeRevision ||
                    ![4, 5].includes(loaded.schemaVersion | 0) ||
                    (typeof raw.uid === 'string' && loaded.uid !== raw.uid)) {
                    return { success: false, error: 'React project load was rejected or did not replace state',
                        applied, schemaVersion: loaded?.schemaVersion };
                }
                // This contract waits for React to commit the new project prop,
                // then rebuilds that project's scene. An old scene's nonzero
                // mesh count cannot complete a second A -> B load.
                const scene = await react.buildGeometryData();
                const current = react.getProject();
                const models = react.allModels();
                const status = typeof react.geometryStatus === 'function'
                    ? react.geometryStatus() : null;
                const sceneReady = current === loaded && scene >= 0 &&
                    (models.length === 0 || (scene > 0 && status?.renderMeshesCount > 0));
                return {
                    success: sceneReady,
                    error: sceneReady ? undefined : 'New React scene did not build for the loaded project',
                    applied,
                    schemaVersion: current?.schemaVersion,
                    modelCount: models.length,
                    renderMeshes: scene,
                    sceneReady,
                };
            }
            if (typeof window.allModels !== 'function') {
                throw new Error('Legacy model inventory is unavailable');
            }
            const before = state;
            const applied = window._applyProjectData(raw, 'headless-load',
                { clearRemotePath: true });
            const models = window.allModels();
            const scene = (typeof renderMeshes !== 'undefined') ? renderMeshes.length : 0;
            const sceneReady = models.length === 0 || scene > 0;
            const success = applied === true && state !== before &&
                (state.schemaVersion | 0) === 4 &&
                (typeof raw.uid !== 'string' || state.uid === raw.uid) && sceneReady;
            return {
                success,
                error: success ? undefined : 'Legacy project load did not complete',
                applied,
                schemaVersion: state.schemaVersion,
                modelCount: models.length,
                renderMeshes: scene,
                sceneReady,
            };
        } catch (error) {
            return { success: false, error: error.message };
        }
    })()"""
    return script.replace("__PROJECT_TEXT__", json.dumps(project_text))


_BROWSER_READY_PROBE = (
    "!!(typeof window._applyProjectData === 'function' && "
    "((window.__dcdtuni && window.__dcdtuni.getProject && "
    "  window.__dcdtuni.allModels) || "
    " typeof window.allModels === 'function'))"
)



def _browser_max_fps() -> float:
    """Bound only the server-owned software preview, leaving user views alone."""
    raw = os.environ.get("DCDTUNI_BROWSER_MAX_FPS")
    if raw is None:
        return 4.0 if os.environ.get("DCDTUNI_BROWSER_SOFTWARE") == "1" else 0.0
    try:
        fps = float(raw)
    except ValueError as exc:
        raise ValueError("DCDTUNI_BROWSER_MAX_FPS must be 0 or between 1 and 60") from exc
    if not math.isfinite(fps) or (fps != 0 and not 1 <= fps <= 60):
        raise ValueError("DCDTUNI_BROWSER_MAX_FPS must be 0 or between 1 and 60")
    return fps


def _browser_frame_budget_script(fps: float) -> str:
    """Batch RAF callbacks with native timestamps and cancellation semantics."""
    if fps == 0:
        return ""
    if not math.isfinite(fps) or not 1 <= fps <= 60:
        raise ValueError("Browser frame budget must be between 1 and 60 FPS")
    return r"""(() => {
        const nativeRequest = window.requestAnimationFrame.bind(window);
        const nativeCancel = window.cancelAnimationFrame.bind(window);
        const callbacks = new Map(), queued = new Set();
        const interval = 1000 / __FPS__;
        let nextId = 1, scheduled = null, last = -Infinity, paused = false;
        function schedule() {
            if (!paused && queued.size && scheduled === null) {
                scheduled = nativeRequest(dispatch);
            }
        }
        function dispatch(timestamp) {
            scheduled = null;
            if (timestamp - last < interval) {
                schedule();
                return;
            }
            last = timestamp;
            // Each admitted callback gets its own native invocation on the
            // next frame. This preserves the browser's microtask checkpoint
            // between callbacks, including cancellation by a Promise chain.
            for (const id of queued) {
                const entry = callbacks.get(id);
                entry.nativeId = nativeRequest(nativeTimestamp => {
                    if (!callbacks.has(id)) return;
                    callbacks.delete(id);
                    entry.callback.call(window, nativeTimestamp);
                });
            }
            queued.clear();
        }
        window.requestAnimationFrame = callback => {
            if (typeof callback !== 'function') {
                throw new TypeError('requestAnimationFrame callback must be a function');
            }
            const id = nextId++;
            callbacks.set(id, {callback, nativeId: null});
            queued.add(id);
            schedule();
            return id;
        };
        window.cancelAnimationFrame = id => {
            // Match the native unsigned-long conversion for string/fractional
            // handles too; callback IDs used by React remain ordinary numbers.
            id = Number(id) >>> 0;
            const entry = callbacks.get(id);
            if (entry?.nativeId !== null && entry?.nativeId !== undefined) {
                nativeCancel(entry.nativeId);
            }
            callbacks.delete(id);
            queued.delete(id);
            if (!queued.size && scheduled !== null) {
                nativeCancel(scheduled);
                scheduled = null;
            }
        };
        Object.defineProperty(window, '__dcdtuniAutomationFrames', {value: {
            pause() {
                paused = true;
                if (scheduled !== null) nativeCancel(scheduled);
                scheduled = null;
                // Rebuild in registration order when both admitted and newly
                // queued callbacks exist at the idle boundary.
                queued.clear();
                for (const [id, entry] of callbacks) {
                    if (entry.nativeId !== null) nativeCancel(entry.nativeId);
                    entry.nativeId = null;
                    queued.add(id);
                }
            },
            resume() {
                if (!paused) return;
                paused = false;
                last = -Infinity;
                schedule();
            },
        }});
    })()""".replace("__FPS__", json.dumps(fps))


def _browser_worker(page_url: str, state: _BrowserWorkerState) -> None:
    resources = {}
    try:
        _run_browser_worker(page_url, state, resources)
    except Exception as exc:
        state.start_error = str(exc)
        print(f"[browser-session] worker stopped: {exc}", flush=True)
    finally:
        with _browser_init_lock:
            state.closing = True
            state.ready.clear()
        errors = []
        # Playwright is thread-bound: attempt each owned cleanup on this thread.
        for name, method in (("context", "close"), ("browser", "close"), ("pw", "stop")):
            resource = resources.get(name)
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        _finish_browser_worker(state, errors)

def _run_browser_worker(page_url: str, state: _BrowserWorkerState, resources: dict) -> None:
    """Dedicated thread that owns the Playwright browser session.

    Runs a REAL browser window on an X display (Xvfb) by default, with WebGL on
    the NVIDIA GPU. That is the whole point: this server drives a Three.js
    editor, and the automation's screenshots are supposed to show the 3D scene.
    """
    from playwright.sync_api import sync_playwright

    def _on_console(msg):
        _browser_console_logs.append(f"[{msg.type}] {msg.text}")
        if len(_browser_console_logs) > 2000:
            del _browser_console_logs[:1000]

    def _on_error(error):
        _browser_errors.append(f"[PAGE_ERROR] {error.message}")
        if len(_browser_errors) > 500:
            del _browser_errors[:250]

    display = None if _BROWSER_HEADLESS else _ensure_x_display()
    headless = _BROWSER_HEADLESS or display is None
    state.headless = headless
    env = dict(os.environ)
    if display:
        env["DISPLAY"] = display

    max_fps = _browser_max_fps()
    idle_software_preview = max_fps > 0 and os.environ.get("DCDTUNI_BROWSER_SOFTWARE") == "1"
    pw = sync_playwright().start()
    resources["pw"] = pw
    browser = pw.chromium.launch(headless=headless, args=_browser_launch_args(), env=env)
    resources["browser"] = browser
    mode = "headless=new" if headless else f"head on {display}"
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    resources["context"] = context
    if max_fps:
        context.add_init_script(_browser_frame_budget_script(max_fps))
        print(f"[browser-session] automation preview frame budget: {max_fps:g} FPS", flush=True)

    # Give the worker its own session, so the PAGE can call the API.
    #
    # The loopback bypass is GET-only on purpose ("state-mutating POSTs still go
    # through normal auth"), and widening it would hand every process on this
    # shared host the right to start jobs. So the worker authenticates instead,
    # exactly like a user, and holds one ordinary session.
    #
    # Without this the page can build and export but cannot RUN: React's own
    # submit path posts /api/run-mesh from the page, got 401, and the app's 401
    # interceptor left runJob's promise pending forever — a solve that neither
    # started nor failed. The legacy app has the same hole and never noticed,
    # because its pipeline posts run-mesh with curl from outside instead of
    # using the button.
    if AUTH_MGR is not None:
        try:
            token = AUTH_MGR.create_session("admin", "admin", "127.0.0.1")
            context.add_cookies([{
                "name": _auth_cookie_name_for_port(urlparse(page_url).port),
                "value": token,
                "domain": "127.0.0.1",
                "path": "/",
                "httpOnly": True,
                "sameSite": "Lax",
            }])
            print("[browser-session] worker session issued", flush=True)
        except Exception as e:
            print(f"[browser-session] could not issue a worker session: {e}", flush=True)

    page = context.new_page()
    page.on("console", _on_console)
    page.on("pageerror", _on_error)

    # The React build bundles its own dependencies, so there is nothing to inline
    # and no page.route interception to deadlock against — which is the only
    # reason /api/headless-html ever existed. Load the app the way a user does.
    #
    # `domcontentloaded`, not `load`: index.html links Google Fonts, and on a host
    # that cannot reach fonts.googleapis.com the load event never fires. The fonts
    # are presentation-only with local fallbacks, so waiting for them would trade
    # a 60 s timeout for a different typeface.
    target = page_url
    # React installs the automation namespace before App's effect installs the
    # interactive loader. Wait for BOTH, or the first export races that effect.
    # The legacy editor only has its global loader and model inventory.
    ready_desc = "interactive project loader and editor model inventory"

    print(f"[browser-session] {mode}; loading {target} ...", flush=True)
    page.goto(target, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function(_BROWSER_READY_PROBE, timeout=30000)
    page.wait_for_timeout(500)

    # Report what the GPU actually is, every start. This is the one fact that
    # silently degrades — an ICD that stops resolving turns every screenshot
    # into a software render, and nothing else in the log would say so.
    try:
        renderer = page.evaluate("""() => {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl2') || c.getContext('webgl');
            if (!gl) return 'NO WEBGL';
            const e = gl.getExtension('WEBGL_debug_renderer_info');
            return e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : '(no debug ext)';
        }""")
    except Exception as e:
        renderer = f"(probe failed: {e})"
    print(f"[browser-session] ready ({ready_desc}); WebGL: {renderer}", flush=True)
    if idle_software_preview:
        page.evaluate("() => window.__dcdtuniAutomationFrames.pause()")
    state.ready.set()

    # Command loop: receive (fn_name, args, result_event, result_holder) tuples
    while True:
        try:
            cmd = state.queue.get(timeout=1.0)
        except _queue.Empty:
            continue
        if cmd is None:  # shutdown sentinel
            break
        fn_name, args, result_event, result_holder = cmd
        with _browser_init_lock:
            state.active = fn_name
        try:
            if idle_software_preview:
                page.evaluate("() => window.__dcdtuniAutomationFrames.resume()")
            if fn_name == "exec_js":
                result_holder["result"] = page.evaluate(args[0])
            elif fn_name == "load_project":
                with open(args[0]) as f:
                    proj_data = f.read()
                result_holder["result"] = page.evaluate(
                    _browser_load_project_script(proj_data))
                # WAIT for the scene to actually hold the geometry before saying
                # the load is done.
                #
                # The legacy app rebuilds inside _applyProjectData, so its answer
                # is true the moment it returns. React schedules the rebuild as
                # an effect, so at that instant the scene is EMPTY and fills a
                # beat later — measured at 0 meshes on return and 37 one second
                # after. Returning there would be a lie the pipeline acts on:
                # /dc-builder loads a project and immediately exports STL, and an
                # export against an unbuilt scene writes an empty file without
                # failing. Every downstream step then runs on no geometry.
                res = result_holder.get("result") or {}
                if res.get("success") and res.get("modelCount") and not res.get("sceneReady"):
                    try:
                        page.wait_for_function(
                            "() => { const s = window.__dcdtuni && window.__dcdtuni.geometryStatus"
                            "   ? window.__dcdtuni.geometryStatus() : null;"
                            "  return s ? s.renderMeshesCount > 0 : true; }",
                            timeout=30000,
                        )
                        # Guarded, like the wait above it. The legacy app has
                        # no `__dcdtuni` — an unguarded call there threw, the
                        # except swallowed it, and a perfectly good load came
                        # back reporting 0 meshes and a "scene build timeout".
                        res["renderMeshes"] = page.evaluate(
                            "() => {"
                            "  if (window.__dcdtuni && window.__dcdtuni.geometryStatus)"
                            "    return window.__dcdtuni.geometryStatus().renderMeshesCount;"
                            "  return (typeof renderMeshes !== 'undefined') ? renderMeshes.length : 0;"
                            "}"
                        )
                    except Exception as e:
                        # Surfaced, not swallowed: a project whose geometry never
                        # builds is exactly the case this wait exists to catch.
                        res["renderMeshes"] = 0
                        res["sceneBuildTimeout"] = str(e)[:200]
            elif fn_name == "get_state_summary":
                result_holder["result"] = page.evaluate("""(() => {
                    // V4-native (T15i): read the uid-keyed construction buckets via the
                    // public allModels() helper and the scattered V4 fields directly.
                    const s = state;
                    const all = (typeof window.allModels === 'function') ? window.allModels() : [];
                    const facePatchCount = (faces) => {
                        if (!faces) return 0;
                        const list = Array.isArray(faces) ? faces : Object.values(faces);
                        return list.reduce((n, f) => n + ((f && Array.isArray(f.patches)) ? f.patches.length : 0), 0);
                    };
                    const sz = (m) => (m.geometry && m.geometry.size) || {};
                    const models = all.map(m => ({
                        id: m.uid, kind: m.kind,
                        name: m.meta && m.meta.name,
                        enabled: m.meta && m.meta.enabled,
                        anchor: m.geometry && m.geometry.location,
                        base: { W: sz(m).x, H: sz(m).y, D: sz(m).z },
                        patchCount: facePatchCount(m.geometry && m.geometry.faces),
                        cfd: m.cooling || null,
                        equipmentCount: (m.kind === 'rack' && m.constructions && m.constructions.servers)
                            ? Object.keys(m.constructions.servers).length : 0
                    }));
                    return {
                        modelCount: all.length,
                        models: models,
                        inputUnit: s.meta && s.meta.inputUnit,
                        selection: (window.uiState && window.uiState.selection) || null,
                        cfdRoom: null
                    };
                })()""")
            elif fn_name in ("export_cfdrun_content", "export_cfd_json"):
                # 2026-04-18 rename: primary name is `export_cfdrun_content`.
                # Legacy `export_cfd_json` accepted for a deprecation window.
                # V2 cfdrun (schemaVersion 2). buildCfdrunJsonV2 is async; page.evaluate
                # awaits the returned promise. The legacy buildCfdExportJson alias emits a
                # V1 manifest (schemaVersion None) that the runner now rejects.
                #
                # The world-matrix flush is the caller's job ONLY in the legacy
                # app, where `buildCfdrunJsonV2` reads matrixWorld and does not
                # refresh it. React's `buildManifest` flushes inside, so asking
                # for `wrapperGroup` there would throw on a name that app has no
                # reason to expose.
                flush = ("" if APP_MODE == "react"
                         else "try { wrapperGroup.updateMatrixWorld(true); } catch(e) {}")
                result_holder["result"] = page.evaluate("""(async () => {
                    try {
                        %s
                        // React hangs the builder off the automation contract;
                        // the legacy editor exposes it as a plain global. Same
                        // manifest either way, so pick whichever app is loaded.
                        const build = (window.__dcdtuni && window.__dcdtuni.buildCfdrunJsonV2)
                            ? window.__dcdtuni.buildCfdrunJsonV2.bind(window.__dcdtuni)
                            : window.buildCfdrunJsonV2;
                        const json = await build();
                        return { success: true, data: json };
                    } catch(e) {
                        return { success: false, error: e.message, stack: e.stack };
                    }
                })()""" % flush)
            elif fn_name == "screenshot":
                if idle_software_preview:
                    # Allow the resumed scene and its UI overlays to draw
                    # before capture, even after an extended idle period.
                    page.evaluate("() => new Promise(resolve => "
                                  "requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                screenshot_dir = HTML_DIR.parent / "scripts" / "screenshots"
                screenshot_dir.mkdir(exist_ok=True)
                path = screenshot_dir / f"{args[0]}.png"
                page.screenshot(path=str(path), full_page=False)
                result_holder["result"] = str(path)
            elif fn_name == "click":
                try:
                    page.click(args[0], timeout=5000)
                    page.wait_for_timeout(500)
                    result_holder["result"] = {"success": True, "selector": args[0]}
                except Exception as e:
                    result_holder["result"] = {"success": False, "selector": args[0], "error": str(e)}
            elif fn_name == "click_text":
                try:
                    page.click(f"button:has-text('{args[0]}')", timeout=5000)
                    page.wait_for_timeout(500)
                    result_holder["result"] = {"success": True, "text": args[0]}
                except Exception as e:
                    result_holder["result"] = {"success": False, "text": args[0], "error": str(e)}
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            if idle_software_preview:
                try:
                    page.evaluate("() => window.__dcdtuniAutomationFrames.pause()")
                except Exception as e:
                    result_holder.setdefault("error", str(e))
            with _browser_init_lock:
                state.admitted -= 1
                state.active = None
            result_event.set()


def _ensure_browser(handler) -> bool:
    """Lazily create a fresh worker; never reuse a closing generation."""
    global _browser_state, _browser_cmd_q, _browser_ready, _browser_thread
    deadline = time.monotonic() + 120
    while True:
        with _browser_init_lock:
            state = _browser_state
            if state is None:
                state = _BrowserWorkerState()
                _browser_state = state
                _browser_cmd_q = state.queue
                _browser_ready = state.ready
                port = handler.server.server_address[1]
                state.thread = threading.Thread(target=_browser_worker,
                    args=(f"http://127.0.0.1:{port}/", state), daemon=True, name="browser-worker")
                _browser_thread = state.thread
                state.thread.start()
            closing = state.closing
            if not closing and state.ready.is_set():
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if closing:
            if not state.stopped.wait(timeout=remaining) or state.cleanup_error:
                return False
            continue
        # Initialization failure must wake the HTTP waiter without a 120s stall.
        while time.monotonic() < deadline:
            if state.ready.wait(timeout=min(.1, max(0, deadline - time.monotonic()))):
                with _browser_init_lock:
                    if _browser_state is state and not state.closing:
                        return True
                break
            if state.stopped.is_set():
                return False

def _browser_call(fn_name: str, *args, timeout: float | None = None):
    """Admit a bounded call atomically; timeout never implies worker idle."""
    if timeout is None:
        timeout = 120.0 if fn_name == "load_project" else 30.0
    result_holder: dict = {}
    result_event = threading.Event()
    with _browser_init_lock:
        state = _browser_state
        q = _browser_cmd_q
        if q is None or (state is not None and (state.closing or not state.ready.is_set())):
            raise RuntimeError("Browser worker is not ready or is closing")
        if state is not None:
            state.admitted += 1
        q.put((fn_name, args, result_event, result_holder))
    if not result_event.wait(timeout=timeout):
        raise TimeoutError(f"Browser call '{fn_name}' timed out after {timeout}s")
    if "error" in result_holder:
        raise RuntimeError(result_holder["error"])
    return result_holder.get("result")

def _shutdown_browser() -> None:
    """Bounded server shutdown; only the owned generation gets a sentinel."""
    _close_browser(timeout=5.0, idle_only=False)


# ── Post-processing: binary pipeline ──
# Lazy-import CachedPostProcessor to avoid pulling in heavy deps
# (numpy/scipy/pandas/trimesh/sympy/kubernetes ~2s) at startup.
_CachedPostProcessor = None

def _get_cached_pp():
    global _CachedPostProcessor
    if _CachedPostProcessor is None:
        from backend.aircooling.runner.postprocess import CachedPostProcessor
        _CachedPostProcessor = CachedPostProcessor
    return _CachedPostProcessor.get_instance()


class PostProcessAIWorkerManager:
    """Thin JSON-RPC bridge to the persistent adaptive-inference worker."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._worker_script = Path(__file__).with_name("pp_ai_worker.py")
        self._worker_python = self._resolve_worker_python()

    @staticmethod
    def _inactive_status(last_error: str | None = None) -> dict:
        return {
            "ready": False,
            "device": "",
            "modelPath": "",
            "casePath": "",
            "caseName": "",
            "bounds": None,
            "lastError": last_error,
            "lastResult": None,
        }

    @staticmethod
    def _resolve_worker_python() -> Path:
        override = os.environ.get("PP_AI_WORKER_PYTHON", "").strip()
        candidates = []
        if override:
            candidates.append(Path(override).expanduser())

        py_path = Path(sys.executable).resolve()
        if "/envs/" in str(py_path):
            conda_base = Path(str(py_path).split("/envs/")[0]).parent
        else:
            conda_base = Path.home() / "miniconda3"
        candidates.append(conda_base / "envs" / "FVGN-pt2.7" / "bin" / "python")
        candidates.append(Path.home() / "miniconda3" / "envs" / "FVGN-pt2.7" / "bin" / "python")

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise FileNotFoundError("Cannot locate FVGN-pt2.7 python interpreter for AI worker")

    def _drain_stderr(self) -> None:
        if not self._proc or self._proc.stderr is None:
            return
        for line in self._proc.stderr:
            text = line.rstrip()
            if not text:
                continue
            self._stderr_lines.append(text)
            if len(self._stderr_lines) > 200:
                del self._stderr_lines[:100]

    def _ensure_process(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._stderr_lines.clear()
        self._proc = subprocess.Popen(
            [str(self._worker_python), "-u", str(self._worker_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(REPO_ROOT := Path(__file__).resolve().parents[2]),
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _stop_process_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self._stderr_thread = None
        if proc is None:
            return

        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(json.dumps({"action": "shutdown"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass

        if proc.poll() is not None:
            return

        try:
            proc.terminate()
            proc.wait(timeout=2.0)
            return
        except Exception:
            pass

        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass

    def recent_stderr(self) -> list[str]:
        return list(self._stderr_lines[-50:])

    def _request(self, payload: dict, timeout: float = 300.0) -> dict:
        with self._lock:
            self._ensure_process()
            assert self._proc is not None
            if self._proc.stdin is None or self._proc.stdout is None:
                raise RuntimeError("AI worker pipes are not available")

            try:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()

                ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
                if not ready:
                    raise TimeoutError(f"AI worker request timed out after {timeout:.0f}s")

                line = self._proc.stdout.readline()
                if not line:
                    rc = self._proc.poll()
                    raise EOFError(
                        f"AI worker exited unexpectedly with code {rc}. "
                        + "\n".join(self.recent_stderr()[-10:])
                    )
                response = json.loads(line)
            except (BrokenPipeError, EOFError, OSError, TimeoutError, json.JSONDecodeError) as exc:
                self._stop_process_locked()
                raise RuntimeError(f"AI worker transport failed: {exc}") from exc

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "unknown AI worker error"))
            return response

    def start(self, model_path: str, case_path: str) -> dict:
        return self._request(
            {"action": "start", "modelPath": model_path, "casePath": case_path},
            timeout=900.0,
        )

    def status(self) -> dict:
        with self._lock:
            if self._proc is None:
                status = self._inactive_status()
                status["stderr"] = self.recent_stderr()
                return status
            if self._proc.poll() is not None:
                rc = self._proc.returncode
                self._stop_process_locked()
                status = self._inactive_status(f"AI worker is not running (exit code {rc})")
                status["stderr"] = self.recent_stderr()
                return status

        try:
            response = self._request({"action": "status"}, timeout=10.0)
            status = response.get("status", {})
        except Exception as exc:
            status = self._inactive_status(str(exc))
        status["stderr"] = self.recent_stderr()
        return status

    def infer(self, box: dict) -> dict:
        return self._request({"action": "infer", "box": box}, timeout=900.0)

    def stop(self) -> None:
        with self._lock:
            self._stop_process_locked()


_PP_AI_WORKER: PostProcessAIWorkerManager | None = None


def _get_pp_ai_worker() -> PostProcessAIWorkerManager:
    global _PP_AI_WORKER
    if _PP_AI_WORKER is None:
        _PP_AI_WORKER = PostProcessAIWorkerManager()
    return _PP_AI_WORKER


# ---------------------------------------------------------------------------
# OpenFOAM probes FO output parser (scalar / vector)
# ---------------------------------------------------------------------------

def parse_probes_file(path, *, is_vector: bool, since: int = -1):
    """Parse an OpenFOAM probes FO output file (scalar or vector).

    Returns (steps, values) where:
      - steps: list[int] of time steps (integers; OF writes int when possible)
      - values: list[list[float]] for scalar, list[list[list[float]]] for vector
               outer index = step, inner index = probe index

    Format (scalar):
        # Probe 0 (x y z)
        # Probe N (x y z)
        # Time    probe0    probeN
            10  299.5      300.1

    Format (vector):
        # Probe 0 (x y z)
        # Time       probe0               probe1
            10   (0.1 0.2 0.3)       (0.4 0.5 0.6)

    `since` filters: only rows with step > since are returned.
    Missing / empty files return ([], []).
    """
    import re
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return [], []
    text = p.read_text(encoding="utf-8", errors="ignore")
    steps = []
    values = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if is_vector:
            vecs = re.findall(r"\(([^)]+)\)", line)
            if not vecs:
                continue
            # First column before any paren is the time
            head = line.split("(", 1)[0].strip()
            try:
                t = float(head)
            except ValueError:
                continue
            step = int(t) if t == int(t) else t
            if step <= since:
                continue
            row = [[float(x) for x in v.split()] for v in vecs]
            steps.append(step)
            values.append(row)
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                t = float(parts[0])
            except ValueError:
                continue
            step = int(t) if t == int(t) else t
            if step <= since:
                continue
            try:
                row = [float(x) for x in parts[1:]]
            except ValueError:
                continue
            steps.append(step)
            values.append(row)
    return steps, values


# ---------------------------------------------------------------------------
# Run-directory helpers + GET /api/probes-latest handler
# ---------------------------------------------------------------------------

def _run_dir_timestamp(name: str):
    """The ``%Y-%m-%d-%H-%M-%S`` the runner stamps a run dir with, or None."""
    import datetime as _dt
    try:
        return _dt.datetime.strptime(name[:19], "%Y-%m-%d-%H-%M-%S")
    except ValueError:
        return None


def _find_latest_run_dir(case_dir, prefix=None, started_at=None):
    """Return newest <case>/log/<run>/ with a serial or partitioned mesh.

    *prefix* keeps only directories whose name starts with it. *started_at*
    keeps only directories stamped at or after that time (second resolution,
    the runner stamps the dir after the job starts), which is how the probe
    poller avoids a previous solve's data without tripping over a minute
    boundary between the click and the runner's timestamp.
    """
    from pathlib import Path as _P
    from backend.aircooling.runner.mesh_layout import mesh_exists
    log_dir = _P(case_dir) / "log"
    if not log_dir.exists():
        return None
    floor = started_at.replace(microsecond=0) if started_at else None
    for d in sorted(log_dir.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if prefix and not d.name.startswith(prefix):
            continue
        if floor is not None:
            stamp = _run_dir_timestamp(d.name)
            if stamp is None or stamp < floor:
                continue
        if mesh_exists(d):
            return d
    return None


def _live_solve_run_dir(case_dir):
    """The run dir the live solve writes to: the pinned one, else by start time."""
    from backend.aircooling.runner.mesh_layout import mesh_exists
    pinned = _LATEST_SOLVE_RUN_DIR
    if pinned is not None and mesh_exists(pinned):
        return pinned
    if _LATEST_SOLVE_STARTED is not None:
        return _find_latest_run_dir(case_dir, started_at=_LATEST_SOLVE_STARTED)
    return _find_latest_run_dir(case_dir, prefix=_LATEST_SOLVE_PREFIX or None)


def _read_write_interval(run_dir):
    """Parse writeInterval from run_dir/system/controlDict (probes subdict)."""
    import re
    cd = run_dir / "system" / "controlDict"
    if not cd.exists():
        return 10  # default fallback
    txt = cd.read_text(encoding="utf-8", errors="ignore")
    # Match writeInterval anywhere after 'probes'
    m = re.search(r"probes\s*\{[^}]*?writeInterval\s+([\d.eE+-]+)\s*;",
                  txt, re.DOTALL)
    return float(m.group(1)) if m else 10


def handle_probes_latest(handler):
    """GET /api/probes-latest?since=<int>

    Returns incremental probe values across 5 fields (T, U, p, p_rgh, rho).
    Uses _LATEST_SOLVE_PREFIX to only read probes from the current solve run,
    preventing stale data from a previous solve from leaking into the chart.
    """
    from urllib.parse import urlparse, parse_qs
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    run_dir = _live_solve_run_dir(cd)
    if run_dir is None:
        if _LATEST_SOLVE_PREFIX or _LATEST_SOLVE_STARTED is not None:
            _send_json(handler, 200, {
                "steps": [], "writeInterval": 10,
                "fields": {k: [] for k in ("T", "U", "p", "p_rgh", "rho")},
            })
            return
        _send_json(handler, 404, {"error": "No run directory found."})
        return
    probes_root = run_dir / "postProcessing" / "probes"
    if not probes_root.exists():
        # Solver just started — probes FO hasn't written yet
        _send_json(handler, 200, {
            "steps": [], "writeInterval": _read_write_interval(run_dir),
            "fields": {k: [] for k in ("T", "U", "p", "p_rgh", "rho")},
        })
        return
    # Pick earliest subdirectory (probes startTime; usually "0")
    time_dirs = sorted(d for d in probes_root.iterdir() if d.is_dir())
    if not time_dirs:
        _send_json(handler, 200, {
            "steps": [], "writeInterval": _read_write_interval(run_dir),
            "fields": {k: [] for k in ("T", "U", "p", "p_rgh", "rho")},
        })
        return
    t0 = time_dirs[0]

    # Parse `since` query
    qs = parse_qs(urlparse(handler.path).query)
    try:
        since = float(qs.get("since", ["-1"])[0])
    except (ValueError, TypeError):
        since = -1

    fields = {}
    master_steps = None
    for name in ("T", "U", "p", "p_rgh", "rho"):
        is_vec = (name == "U")
        steps, values = parse_probes_file(t0 / name, is_vector=is_vec,
                                          since=since)
        fields[name] = values
        if master_steps is None and steps:
            master_steps = steps

    _send_json(handler, 200, {
        "steps": master_steps or [],
        "writeInterval": _read_write_interval(run_dir),
        "fields": fields,
    })


def _parse_probe_locations(run_dir):
    """Extract probeLocations list from run_dir/system/controlDict.

    Returns list[tuple[float,float,float]] or [] if missing.
    """
    import re
    cd = run_dir / "system" / "controlDict"
    if not cd.exists():
        return []
    txt = cd.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"probeLocations\s*\(\s*(.*?)\s*\)\s*;",
                  txt, re.DOTALL)
    if not m:
        return []
    locs = []
    for tup in re.findall(r"\(\s*([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s*\)",
                          m.group(1)):
        locs.append([float(x) for x in tup])
    return locs


def _convert_temp_from_k(value_k, unit):
    """Convert Kelvin to user unit. unit in {K, C, F}; default C on unknown."""
    if unit == "K":
        return value_k
    if unit == "F":
        return (value_k - 273.15) * 9.0 / 5.0 + 32.0
    return value_k - 273.15  # C


def handle_harvest_sensor_results(handler):
    """POST /api/harvest-sensor-results — read last probe row, write JSON."""
    import json as _json
    from datetime import datetime as _dt
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    run_dir = _live_solve_run_dir(cd) or _find_latest_run_dir(cd)
    if run_dir is None:
        _send_json(handler, 404, {"error": "No run directory found."})
        return
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
        body = _json.loads(handler.rfile.read(length) or b"{}")
    except Exception as exc:
        _send_json(handler, 400, {"error": f"Invalid body: {exc}"})
        return
    case_name = body.get("caseName") or "case"
    sensor_names = list(body.get("sensorNames") or [])
    unit_t = body.get("unitTemperature", "C")

    probes_root = run_dir / "postProcessing" / "probes"
    time_dirs = sorted(d for d in probes_root.iterdir() if d.is_dir()) \
        if probes_root.exists() else []
    if not time_dirs:
        _send_json(handler, 500, {"error": "No probe data found in run directory"})
        return
    t0 = time_dirs[0]

    fields_last = {}
    last_step = None
    # T and U are always required (required-primary). p/p_rgh/rho are optional:
    # Boussinesq incompressible solvers (buoyantBoussinesqSimpleFoam) don't
    # write rho; p is typically present but some variants omit it.
    REQUIRED = {"T", "U"}
    for name in ("T", "U", "p", "p_rgh", "rho"):
        is_vec = (name == "U")
        steps, values = parse_probes_file(t0 / name, is_vector=is_vec, since=-1)
        if not steps:
            if name in REQUIRED:
                _send_json(handler, 500, {"error": f"No data in probes/{name}"})
                return
            # Optional field missing (e.g. rho for Boussinesq) — skip.
            fields_last[name] = None
            continue
        fields_last[name] = values[-1]
        if last_step is None:
            last_step = steps[-1]

    n_columns = len(fields_last["T"])
    if len(sensor_names) != n_columns:
        _send_json(handler, 500, {
            "error": f"sensor count mismatch: frontend={len(sensor_names)}, probes={n_columns}"
        })
        return

    locations = _parse_probe_locations(run_dir)

    def _pick(field, i):
        row = fields_last.get(field)
        if row is None or i >= len(row):
            return None
        # OpenFOAM probes sentinel for "point outside mesh" is -1e+300; map
        # to null so downstream tools see an explicit "no data" rather than
        # a ~-10^300 outlier. Applies to both scalars and vector components.
        val = row[i]
        if isinstance(val, (int, float)) and val < -1e299:
            return None
        if isinstance(val, list) and val and all(
            isinstance(v, (int, float)) and v < -1e299 for v in val
        ):
            return None
        return val

    sensors = []
    for i, name in enumerate(sensor_names):
        t_k = fields_last["T"][i]
        t_final = None if (isinstance(t_k, (int, float)) and t_k < -1e299) \
                       else _convert_temp_from_k(t_k, unit_t)
        sensors.append({
            "name": name,
            "location": locations[i] if i < len(locations) else None,
            "T_final": t_final,
            "U_final": _pick("U", i),
            "p_final": _pick("p", i),
            "p_rgh_final": _pick("p_rgh", i),
            "rho_final": _pick("rho", i),
        })

    now = _dt.now()
    out_path = (run_dir / "sensorResults"
                / f"{case_name}_sensor_{now.strftime('%H-%M-%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "caseName": case_name,
        "solveRunDir": str(run_dir.relative_to(cd)),
        "finalStep": last_step,
        "writeInterval": _read_write_interval(run_dir),
        "unitLength": "m",
        "unitTemperature": unit_t,
        "generatedAt": now.isoformat(timespec="seconds"),
        "sensors": sensors,
    }
    out_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    _send_json(handler, 200, {"path": str(out_path)})


# ---------------------------------------------------------------------------
# GET /api/check-mesh  — check if polyMesh exists in case dir
# ---------------------------------------------------------------------------

def handle_check_mesh(handler: http.server.BaseHTTPRequestHandler) -> None:
    from backend.aircooling.runner.mesh_layout import mesh_directories

    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if not cd:
        _send_json(handler, 200, {"meshReady": False, "reason": "No case directory set"})
        return
    mesh_dir = cd / "Mesh"
    meshes = mesh_directories(mesh_dir)
    decomposed = bool(meshes) and meshes[0].parent.parent != mesh_dir
    _send_json(handler, 200, {
        "meshReady": bool(meshes),
        "polyMeshPath": str(meshes[0]) if meshes else None,
        "decomposed": decomposed,
        "nPartitions": len(meshes) if decomposed else 0,
    })


# ---------------------------------------------------------------------------
# POST /api/run-check-mesh  — run OpenFOAM checkMesh and stream output
# ---------------------------------------------------------------------------

def handle_run_check_mesh(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Run the full check-only gate against the actual mesh and frozen input."""
    import hashlib
    import signal
    from backend.aircooling.runner.mesh_layout import mesh_directories
    from backend.aircooling.runner.openfoam_patch_area import (
        mesh_identity_sha256, require_nonempty_mesh,
    )
    from backend.aircooling.runner.air_mesh_quality_receipt import (
        record_prechecked_air_mesh_quality,
    )

    started = time.monotonic()
    data = _read_body(handler)
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if not cd:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    cd = Path(cd).resolve()
    mesh_dir = cd / "Mesh"
    requested = data.get("cfdrunFilename")
    if requested is not None and (
        not isinstance(requested, str) or Path(requested).name != requested
        or not requested.endswith(".cfdrun")
    ):
        _send_json(handler, 400, {"error": "cfdrunFilename must name an existing case .cfdrun."})
        return
    try:
        with case_execution_lock(cd):
            meshes = mesh_directories(mesh_dir)
            if not meshes:
                _send_json(handler, 404, {"error": "No complete mesh found. Run mesh generation first."})
                return
            require_nonempty_mesh(mesh_dir)
            candidates = ([cd / "model" / requested] if requested
                          else sorted((cd / "model").glob("*.cfdrun")))
            if requested and not candidates[0].is_file():
                raise ValueError("Selected case .cfdrun does not exist.")
            if len(candidates) > 1:
                raise ValueError("Multiple case inputs exist; specify cfdrunFilename.")
            cfdrun = candidates[0] if candidates else None
            if cfdrun and not cfdrun.resolve().is_relative_to(cd):
                raise ValueError("Selected .cfdrun must stay inside the case directory.")
            input_sha = hashlib.sha256(cfdrun.read_bytes()).hexdigest() if cfdrun else None
            mesh_sha = mesh_identity_sha256(mesh_dir)
    except CaseExecutionBusyError as exc:
        _send_json(handler, 409, {"error": str(exc), "code": "case_busy"})
        return
    except (OSError, ValueError) as exc:
        _send_json(handler, 400, {"error": str(exc)})
        return

    cmd = ["checkMesh", "-case", str(mesh_dir), "-constant", "-allTopology", "-allGeometry"]
    if meshes[0] != mesh_dir / "constant" / "polyMesh":
        cmd = ["mpirun", "-np", str(len(meshes)), *cmd, "-parallel"]
    job_id = str(uuid.uuid4())[:8]
    with JOBS_LOCK:
        JOBS[job_id] = _new_job_state(
            [f"[server] Running: {' '.join(cmd)}"],
            checkMeshOk=None, failedChecks=None,
        )

    def _run() -> None:
        proc = None
        timer = None
        timed_out = threading.Event()
        rc = -1
        try:
            _raise_if_job_cancelled(job_id)
            with case_execution_lock(cd):
                _raise_if_job_cancelled(job_id)
                # Recheck after asynchronous admission, before any subprocess.
                if mesh_identity_sha256(mesh_dir) != mesh_sha:
                    raise ValueError("Mesh changed before checkMesh.")
                if cfdrun and hashlib.sha256(cfdrun.read_bytes()).hexdigest() != input_sha:
                    raise ValueError("Case input changed before checkMesh.")
                (cd / "mesh-quality-receipt.json").unlink(missing_ok=True)
                remaining = 300 - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("checkMesh whole-job budget exhausted (300 s).")
                proc = _spawn_job_process(
                    job_id, cmd, cwd=str(mesh_dir), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    preexec_fn=os.setsid,
                )
                def expire() -> None:
                    timed_out.set()
                    # The launcher and every checker rank share this session.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

                timer = threading.Timer(remaining, expire)
                timer.daemon = True
                timer.start()
                output = []
                for line in iter(proc.stdout.readline, ""):
                    output.append(line)
                    if line.rstrip():
                        with JOBS_LOCK:
                            JOBS[job_id]["lines"].append(line.rstrip())
                proc.wait()
                timer.cancel()
                rc = proc.returncode
                raw = "".join(output)
                (mesh_dir / f"log.checkMesh_api_{job_id}.txt").write_text(raw, encoding="utf-8")
                _raise_if_job_cancelled(job_id)
                if timed_out.is_set():
                    rc = 124
                    raise TimeoutError("checkMesh whole-job budget exhausted (300 s); MPI group stopped.")
                if rc != 0:
                    raise RuntimeError(f"checkMesh process exited {rc}.")
                if mesh_identity_sha256(mesh_dir) != mesh_sha:
                    raise ValueError("Mesh changed during checkMesh.")
                if cfdrun:
                    receipt = record_prechecked_air_mesh_quality(
                        mesh_dir, cfdrun, command=cmd, workdir=mesh_dir,
                        mesh_sha256_before=mesh_sha, cfdrun_sha256_before=input_sha,
                        returncode=rc, stdout=raw, stderr="",
                    )
                    with JOBS_LOCK:
                        JOBS[job_id]["meshQualityReceipt"] = receipt
                    if receipt["status"] != "accepted":
                        rc = 1
                else:
                    with JOBS_LOCK:
                        JOBS[job_id]["lines"].append(
                            "[server] No case .cfdrun; checker verdict has no bound input receipt."
                        )
                # Hashing, durable stdout and receipt writes are in this budget.
                if time.monotonic() - started > 300:
                    rc = 124
                    (cd / "mesh-quality-receipt.json").unlink(missing_ok=True)
                    with JOBS_LOCK:
                        JOBS[job_id].pop("meshQualityReceipt", None)
                    raise TimeoutError("checkMesh whole-job budget exceeded after identity/receipt work (300 s).")
        except _JobCancelledError:
            pass
        except Exception as exc:
            if proc is not None and proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            if rc in (0, -1):
                rc = 1
            with JOBS_LOCK:
                JOBS[job_id]["lines"].append(f"[server] checkMesh failed: {exc}")
        finally:
            if timer:
                timer.cancel()
            _join_job_cleanup(job_id, proc)
            with JOBS_LOCK:
                lines = list(JOBS[job_id]["lines"])
            summary = _summarize_check_mesh_result(lines, rc)
            if not summary["checkMeshOk"] and rc == 0:
                rc = 1
            _finish_job(
                job_id, rc, quality=summary,
                cancelled_receipt=(cd / "mesh-quality-receipt.json")
                if proc is not None else None,
            )

    threading.Thread(target=_run, daemon=True).start()
    _send_json(handler, 200, {"jobId": job_id})


# ---------------------------------------------------------------------------
# GET /api/scan-case-status  — scan case dir for mesh + solve status
# ---------------------------------------------------------------------------

def _scan_case_mesh_status(case_dir: Path) -> dict:
    """Solve readiness requires accepted strict quality for these exact bytes.

    Mesh existence remains a separate fact used by the check-only probe.
    This scan reads an existing receipt; it never launches checkMesh.
    """
    from backend.aircooling.runner.mesh_layout import mesh_exists
    from backend.aircooling.runner.air_mesh_quality_receipt import (
        verify_air_mesh_quality_receipt,
    )

    mesh_dir = case_dir / "Mesh"
    status = {"meshExists": mesh_exists(mesh_dir), "meshReady": False}
    receipt_path = case_dir / "mesh-quality-receipt.json"
    if not status["meshExists"] or not receipt_path.is_file():
        return status
    try:
        with case_execution_lock(case_dir):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            # Refusal needs no expensive full-mesh identity scan.
            if not isinstance(receipt, dict) or receipt.get("status") != "accepted":
                return status
            inputs = [path for path in (case_dir / "model").glob("*.cfdrun")
                      if path.is_file()]
            if len(inputs) != 1:
                return status
            verify_air_mesh_quality_receipt(
                receipt_path, mesh_dir, inputs[0], require_accepted=True,
            )
            status["meshReady"] = True
    except (CaseExecutionBusyError, OSError, ValueError):
        pass
    return status


def handle_scan_case_status(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Return structured status of mesh readiness and all solve log runs."""
    import re
    from backend.aircooling.runner.mesh_layout import available_result_times
    from backend.aircooling.runner.convergence import run_is_transient

    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if not cd:
        _send_json(handler, 200, {
            "meshExists": False,
            "meshReady": False,
            "logRuns": [],
            "solveReady": False,
            "postprocessReady": False,
        })
        return

    # 1. Restore readiness only from the bound strict quality verdict.
    mesh_status = _scan_case_mesh_status(cd)

    # 2. Scan log/ for run directories
    #    Air-mode: YYYY-MM-DD-HH-MM-SS_<name>  (all hyphens)
    #    Liquid:   YYYY-MM-DD_HH-MM-SS          (underscore separator, no suffix)
    log_dirs_to_scan = [cd / "log"]
    liquid_log = cd / "Liquid" / "log"
    if liquid_log.is_dir():
        log_dirs_to_scan.append(liquid_log)
    log_runs = []
    liquid_runs = []
    run_dir_pattern = re.compile(
        r"^\d{4}-\d{2}-\d{2}[-_]\d{2}-\d{2}-\d{2}"
    )

    for log_dir in log_dirs_to_scan:
        if not log_dir.is_dir():
            continue
        for d in log_dir.iterdir():
            if not d.is_dir():
                continue
            result_file = liquid_result_file(d)
            if not run_dir_pattern.match(d.name) and result_file is None:
                continue

            is_liquid = result_file is not None

            if is_liquid:
                liquid_runs.append({
                    "name": d.name,
                    "path": str(d),
                    "type": "liquid",
                    "modified": result_file.stat().st_mtime,
                    "hasResults": True,
                })
                continue

            # A decomposed write is usable only after every rank has written it.
            timesteps = [int(t) if t.is_integer() else t for t in available_result_times(d)]
            has_results = any(t > 0 for t in timesteps)

            latest_ts = timesteps[-1] if timesteps else None

            log_runs.append({
                "name": d.name,
                "path": str(d),
                "runId": hashlib.sha256(str(d.resolve()).encode("utf-8")).hexdigest(),
                "type": "air",
                "timesteps": timesteps,
                "latestTimestep": latest_ts,
                "hasResults": has_results,
                # A transient run's times are seconds the timeline's playhead scrubs.
                "transient": run_is_transient(d),
            })

    # Sort by name descending (newest first)
    log_runs.sort(key=lambda r: r["name"], reverse=True)
    liquid_runs.sort(key=lambda r: (r["modified"], r["name"]), reverse=True)

    solve_ready = any(r["hasResults"] for r in log_runs)
    liquid_ready = len(liquid_runs) > 0
    postprocess_ready = solve_ready or liquid_ready

    _send_json(handler, 200, {
        **mesh_status,
        "logRuns": log_runs,
        "liquidRuns": liquid_runs,
        "solveReady": solve_ready,
        "liquidReady": liquid_ready,
        "postprocessReady": postprocess_ready,
    })


def handle_pp_init(handler):
    """POST /api/pp/init — Initialize post-processor for a run directory."""
    with CASE_DIR_LOCK:
        cd = CASE_DIR
        case_version = CASE_DIR_VERSION
    if cd is None:
        _send_json(handler, 400, {"success": False, "error": "No case directory set."})
        return

    try:
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
        run_dir_rel = body.get("runDir", "")
        time_value = body.get("timeValue")
        if time_value is not None and (
            isinstance(time_value, bool)
            or not isinstance(time_value, (int, float))
            or not math.isfinite(time_value)
        ):
            _send_json(handler, 400, {"success": False, "error": "timeValue must be a finite number"})
            return

        if not run_dir_rel:
            run_dir = _find_latest_run_dir(cd)
            if run_dir is None:
                _send_json(handler, 404, {"success": False, "error": "No run directories found"})
                return
        else:
            requested_path = Path(run_dir_rel).expanduser()
            run_dir = (requested_path if requested_path.is_absolute() else cd / requested_path).resolve()

        if not run_dir.is_relative_to(cd.resolve()):
            _send_json(handler, 400, {"success": False, "code": "pp_run_outside_case",
                                      "error": "Postprocess run must be inside the selected case directory"})
            return

        if not run_dir.exists():
            _send_json(handler, 404, {"success": False, "error": f"Run directory not found: {run_dir}"})
            return

        cpp = _get_cached_pp()
        pp_timeout = float(body.get("timeout", 3600))
        result = cpp.init(run_dir, timeout=pp_timeout, time_value=time_value,
                          case_version=case_version)
        with CASE_DIR_LOCK:
            case_changed = CASE_DIR_VERSION != case_version or CASE_DIR != cd
        if case_changed:
            _send_json(handler, 409, {"success": False, "code": "pp_case_changed",
                                      "error": "Case directory changed during postprocess initialization"})
            return
        _send_json(handler, 200, result)
    except ValueError as e:
        _send_json(handler, 400, {"success": False, "error": str(e)})
    except Exception as e:
        _send_json(handler, 500, {"success": False, "error": str(e)})


def _pp_revision_required(route):
    """Reject a stale caller before touching the process-wide result cache."""
    @functools.wraps(route)
    def guarded(handler, *args, **kwargs):
        from backend.aircooling.runner.postprocess import PostprocessRevisionError

        cpp = _get_cached_pp()
        with CASE_DIR_LOCK:
            case_version = CASE_DIR_VERSION
        try:
            with cpp.require_revision(handler.headers.get("X-PP-Revision"), case_version) as identity:
                handler._pp_identity = identity
                try:
                    return route(handler, *args, **kwargs)
                finally:
                    del handler._pp_identity
        except PostprocessRevisionError as exc:
            _send_json(handler, exc.status, {"code": exc.code, "error": exc.code})

    return guarded


def _send_pp_revision_header(handler):
    handler.send_header("X-PP-Revision", handler._pp_identity["revision"])
    handler.send_header("Access-Control-Expose-Headers", "X-PP-Revision")


@_pp_revision_required
def handle_pp_render_source(handler):
    """Fail closed until the executed run and final mesh have verified lineage."""
    _send_json(handler, 422, {
        "code": "render_source_unavailable",
        "error": "Verified renderer result provenance is not available for this frame",
        **handler._pp_identity,
        "missing": [
            "verified_patch_lineage", "mesh_quality_receipt",
            "run_result_receipt", "field_dimension_receipt",
        ],
    })


@_pp_revision_required
def handle_pp_mesh(handler):
    """GET /api/pp/mesh — Return patch metadata as JSON."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, 400, {"error": "Not initialized. Call /api/pp/init first."})
        return

    try:
        _, meta = cpp.get_mesh()
        fields = cpp.get_available_fields()
        total_points = sum(m["nPoints"] for m in meta)
        total_tris = sum(m["nTriangles"] for m in meta)
        _send_json(handler, 200, {
            "patches": meta,
            "fields": fields,
            "totalPoints": total_points,
            "totalTriangles": total_tris,
            **handler._pp_identity,
        })
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


@_pp_revision_required
def handle_pp_mesh_binary(handler):
    """GET /api/pp/mesh/binary — Return geometry as binary ArrayBuffer."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, 400, {"error": "Not initialized"})
        return

    try:
        binary, _ = cpp.get_mesh()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(binary)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        _send_pp_revision_header(handler)
        handler.end_headers()
        handler.wfile.write(binary)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


@_pp_revision_required
def handle_pp_field(handler, field_name: str):
    """GET /api/pp/field/{name} — Return field scalars as binary ArrayBuffer."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, 400, {"error": "Not initialized"})
        return

    try:
        binary, info = cpp.get_field(field_name)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(binary)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("X-Field-Info", json.dumps(info))
        _send_pp_revision_header(handler)
        handler.end_headers()
        handler.wfile.write(binary)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_pp_runs(handler):
    """GET /api/pp/runs — List available run directories."""
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    try:
        log_dir = cd / "log"
        runs = []
        if log_dir.exists():
            for d in sorted(log_dir.iterdir(), reverse=True):
                if d.is_dir() and not d.name.startswith("."):
                    runs.append({"name": d.name, "path": str(d.relative_to(cd)),
                                 "runId": hashlib.sha256(str(d.resolve()).encode("utf-8")).hexdigest()})
        _send_json(handler, 200, {"runs": runs})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


_MESH_PREVIEW_CACHE = None  # MeshPreviewCache instance (lazy init)

def _mesh_preview_snapshot_key(mesh_dir: Path, case_version: int) -> str:
    """Cheap case/mesh selection token, explicitly not a content receipt.

    A future accepted result endpoint must independently hash all mesh files;
    mtime/size are sufficient only to reject a stale two-request preview flow.
    """
    from backend.aircooling.runner.mesh_layout import mesh_directories

    files = []
    for poly in mesh_directories(mesh_dir):
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            path = poly / name
            stat = path.stat()
            files.append((str(path.relative_to(mesh_dir)), stat.st_size, stat.st_mtime_ns))
    if not files:
        raise ValueError("No complete mesh for preview snapshot")
    material = (str(mesh_dir.resolve()), case_version, files)
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()


def handle_mesh_preview_render_source(handler, query):
    """Bind a preview read to its mesh, without treating its stat key as proof."""
    from backend.aircooling.runner.mesh_layout import mesh_exists

    with CASE_DIR_LOCK:
        cd, case_version = CASE_DIR, CASE_DIR_VERSION
    if cd is None:
        _send_json(handler, 400, {"code": "case_not_selected", "error": "No case directory set"})
        return
    requested = (query.get("meshSnapshotKey") or [None])[0]
    if not isinstance(requested, str) or not re.fullmatch(r"[0-9a-f]{64}", requested):
        _send_json(handler, 400, {"code": "mesh_snapshot_key_required", "error": "meshSnapshotKey is required"})
        return
    mesh_dir = cd / "Mesh"
    if not mesh_exists(mesh_dir):
        _send_json(handler, 404, {"code": "mesh_not_found", "error": "No mesh in selected case"})
        return
    try:
        actual = _mesh_preview_snapshot_key(mesh_dir, case_version)
    except (OSError, ValueError) as exc:
        _send_json(handler, 409, {"code": "mesh_preview_source_stale", "error": str(exc)})
        return
    with CASE_DIR_LOCK:
        changed = CASE_DIR_VERSION != case_version or CASE_DIR != cd
    if changed or actual != requested:
        _send_json(handler, 409, {"code": "mesh_preview_source_stale", "error": "Preview mesh source changed"})
        return
    _send_json(handler, 422, {
        "code": "render_source_unavailable",
        "error": "Verified final-patch lineage is not available for this mesh",
        "meshSnapshotKey": actual,
        "missing": ["verified_patch_lineage", "mesh_quality_receipt"],
    })

def _get_mesh_preview_cache():
    global _MESH_PREVIEW_CACHE
    if _MESH_PREVIEW_CACHE is None:
        from backend.aircooling.runner.postprocess import MeshPreviewCache
        _MESH_PREVIEW_CACHE = MeshPreviewCache()
    return _MESH_PREVIEW_CACHE

def handle_mesh_preview_load(handler):
    """POST /api/mesh-preview/load — Load mesh boundary surfaces."""
    from backend.aircooling.runner.mesh_layout import mesh_exists

    with CASE_DIR_LOCK:
        cd = CASE_DIR
        case_version = CASE_DIR_VERSION
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    try:
        mesh_dir = cd / "Mesh"
        if not mesh_exists(mesh_dir):
            _send_json(handler, 404, {"error": "No mesh found at " + str(mesh_dir)})
            return

        try:
            snapshot_key = _mesh_preview_snapshot_key(mesh_dir, case_version)
        except (OSError, ValueError) as exc:
            _send_json(handler, 409, {"code": "mesh_preview_source_stale", "error": str(exc)})
            return

        loader = _get_mesh_preview_cache().get(mesh_dir)
        binary, meta = loader.load()
        with CASE_DIR_LOCK:
            case_changed = CASE_DIR_VERSION != case_version or CASE_DIR != cd
        try:
            snapshot_stale = _mesh_preview_snapshot_key(mesh_dir, case_version) != snapshot_key
        except (OSError, ValueError):
            snapshot_stale = True
        if case_changed or snapshot_stale:
            _send_json(handler, 409, {"code": "mesh_preview_source_stale", "error": "Mesh changed during preview load"})
            return
        total_pts = sum(m["nPoints"] for m in meta)
        total_tris = sum(m["nTriangles"] for m in meta)
        total_edges = sum(m.get("nEdges", 0) for m in meta)
        counts = loader.read_counts()
        _send_json(handler, 200, {
            "patches": meta,
            "totalPoints": total_pts,
            "totalTriangles": total_tris,
            "totalEdges": total_edges,
            "nCells": counts["nCells"],
            "nBoundaryFaces": counts["nBoundaryFaces"],
            "nBoundarySum": counts["nBoundarySum"],
            "meshSnapshotKey": snapshot_key,
        })
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_mesh_preview_debug(handler):
    """GET /api/mesh-preview/debug — Diagnostic info about mesh face parsing."""
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    try:
        import numpy as np
        from backend.aircooling.runner.postprocess import read_openfoam_case
        mesh_dir = cd / "Mesh"
        mb, _ = read_openfoam_case(mesh_dir, time_value=0)

        boundary = None
        for i in range(mb.n_blocks):
            if mb.keys()[i] == "boundary":
                boundary = mb[i]
                break
        if boundary is None:
            _send_json(handler, 404, {"error": "No boundary block"})
            return

        result = []
        for i in range(boundary.n_blocks):
            name = boundary.keys()[i]
            patch = boundary[i]
            if patch is None or patch.n_points == 0:
                continue
            faces_arr = np.array(patch.faces)
            fidx = 0
            nf = 0
            mx = -1
            vert_counts: dict = {}
            while fidx < len(faces_arr):
                n = int(faces_arr[fidx])
                vert_counts[n] = vert_counts.get(n, 0) + 1
                for k in range(n):
                    vi = int(faces_arr[fidx + 1 + k])
                    if vi > mx:
                        mx = vi
                fidx += 1 + n
                nf += 1
            valid = mx < patch.n_points
            result.append({
                "name": name, "n_points": int(patch.n_points),
                "n_faces": nf, "max_idx": int(mx),
                "vert_counts": vert_counts, "valid": valid,
            })
        _send_json(handler, 200, {"patches": result, "total": len(result)})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_server_patches(handler):
    """GET /api/server-patches — List server inlet/outlet patches and ACU return patches from mesh."""
    import re
    from backend.aircooling.runner.mesh_layout import mesh_boundary_paths
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    boundary_paths = mesh_boundary_paths(cd / "Mesh")
    if not boundary_paths:
        _send_json(handler, 404, {"error": "No mesh found. Run mesh generation first."})
        return
    try:
        # Physical patches may occur on several ranks; expose each name once.
        patch_names = list(dict.fromkeys(
            name
            for boundary_path in boundary_paths
            for name in re.findall(
                r"^\s+(\S+)\s*$", boundary_path.read_text(encoding="utf-8"), re.MULTILINE
            )
        ))
        patch_set = set(patch_names)
        server_results = []
        for name in patch_names:
            low = name.lower()
            is_inlet = "_inlet" in low
            is_outlet = "_outlet" in low
            if is_inlet or is_outlet:
                # Remove only the role token. _p0/_p1 and side qualifiers
                # remain distinct while the selectable patch identity stays exact.
                short_id = re.sub(r"_(?:inlet|outlet)(?=_|$)", "", name, count=1, flags=re.IGNORECASE)
                server_results.append({
                    "patch": name,
                    "shortId": short_id,
                    "type": "inlet" if is_inlet else "outlet",
                })

        acu_return_results = []
        model_dir = cd / "model"
        cfdrun_files = sorted(model_dir.glob("*.cfdrun")) if model_dir.is_dir() else []
        if cfdrun_files:
            import json as _json
            try:
                cfdrun = _json.loads(cfdrun_files[0].read_text(encoding="utf-8"))
                geometry = cfdrun.get("meshGeometry") or {}
                acus = geometry.get("acus", cfdrun.get("acus", {}))
                items = acus.items() if isinstance(acus, dict) else enumerate(acus or [])
                labels = {}
                for key, acu in items:
                    if not isinstance(acu, dict):
                        continue
                    acu_label = acu.get("name") or str(key)
                    for rf in acu.get("returnFaces", []):
                        if isinstance(rf, dict) and rf.get("solidName"):
                            labels[rf["solidName"]] = acu_label

                bindings = cfdrun.get("boundaryBindings")
                if isinstance(bindings, dict) and bindings:
                    # V2+ bindings are the authoritative OpenFOAM patch names;
                    # meshGeometry supplies friendly equipment labels only.
                    candidates = []
                    for binding in bindings.values():
                        if not isinstance(binding, dict) or not (
                                binding.get("role") == "acu_return" or binding.get("bcType") == "acu_return"):
                            continue
                        name = binding.get("patchName") or binding.get("solidName")
                        label = labels.get(binding.get("solidName")) or labels.get(name)
                        if not label:
                            label = (binding.get("objectPath") or "").rsplit(".", 1)[-1] or name
                        candidates.append((name, label))
                else:
                    # V1 stored ACUs directly and did not carry bindings.
                    candidates = list(labels.items())
                seen = set()
                for name, label in candidates:
                    if name and name in patch_set and name not in seen:
                        seen.add(name)
                        acu_return_results.append({"patch": name, "shortId": label, "type": "return"})
            except Exception:
                pass

        _send_json(handler, 200, {
            "patches": server_results,
            "acuReturnPatches": acu_return_results,
        })
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_mesh_preview_binary(handler, query=None):
    """GET /api/mesh-preview/binary — Return mesh geometry as binary."""
    cache = _get_mesh_preview_cache()
    if cache._loader is None:
        _send_json(handler, 400, {"error": "Call /api/mesh-preview/load first"})
        return
    requested = (query or {}).get("meshSnapshotKey", [None])[0]
    mesh_dir = None
    case_version = None
    def snapshot_matches():
        try:
            return _mesh_preview_snapshot_key(mesh_dir, case_version) == requested
        except (OSError, ValueError):
            return False
    if requested is not None:
        with CASE_DIR_LOCK:
            cd, case_version = CASE_DIR, CASE_DIR_VERSION
        if cd is None or not isinstance(requested, str) or not re.fullmatch(r"[0-9a-f]{64}", requested):
            _send_json(handler, 400, {"code": "mesh_snapshot_key_required", "error": "Invalid mesh snapshot key"})
            return
        mesh_dir = cd / "Mesh"
        if (getattr(cache, "_mesh_dir_str", None) != str(mesh_dir.resolve()) or
                not snapshot_matches()):
            _send_json(handler, 409, {"code": "mesh_preview_source_stale", "error": "Preview mesh source changed"})
            return
    try:
        binary, _ = cache._loader.load()
        if requested is not None:
            with CASE_DIR_LOCK:
                case_changed = CASE_DIR_VERSION != case_version or CASE_DIR != cd
            if case_changed or not snapshot_matches():
                _send_json(handler, 409, {"code": "mesh_preview_source_stale", "error": "Preview mesh changed during binary read"})
                return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(binary)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        if requested is not None:
            handler.send_header("X-Mesh-Snapshot-Key", requested)
            handler.send_header("Access-Control-Expose-Headers", "X-Mesh-Snapshot-Key")
        handler.end_headers()
        handler.wfile.write(binary)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


@_pp_revision_required
def handle_pp_streamlines(handler):
    """POST /api/pp/streamlines — Compute streamlines on server."""
    cpp = _get_cached_pp()
    internal = cpp.get_internal_mesh()
    if internal is None:
        _send_json(handler, 400, {"error": "No internal mesh loaded"})
        return

    try:
        import numpy as np
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
        center = body.get("center", list(internal.center))
        radius = body.get("radius", 1.0)
        n_seeds = body.get("nSeeds", 27)
        field_name = body.get("field", "speed")  # "speed" = |U| magnitude (default)

        pv = _get_pyvista_lazy()

        # Generate seed points (uniform in sphere)
        rng = np.random.default_rng(42)
        seed_points = []
        for _ in range(n_seeds):
            u, v, w = rng.uniform(), rng.uniform(), rng.uniform()
            theta = 2 * np.pi * u
            phi = np.arccos(2 * v - 1)
            r = radius * w ** (1.0 / 3.0)
            seed_points.append([
                center[0] + r * np.sin(phi) * np.cos(theta),
                center[1] + r * np.sin(phi) * np.sin(theta),
                center[2] + r * np.cos(phi),
            ])

        seed = pv.PolyData(np.array(seed_points, dtype=np.float64))
        try:
            streams = internal.streamlines_from_source(
                source=seed, vectors="U", max_steps=2000, integration_direction="both",
            )
        except Exception as e:
            _send_json(handler, 500, {"error": f"Streamline computation failed: {e}"})
            return

        if streams is None or streams.n_points == 0:
            # Send empty result in binary format (same wire format as normal response)
            empty_header = json.dumps({"streamlines": [], "scalarRange": [0, 0], "field": field_name}).encode("utf-8")
            empty_payload = struct.pack("<I", len(empty_header)) + empty_header
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("Content-Length", str(len(empty_payload)))
            handler.send_header("Access-Control-Allow-Origin", "*")
            _send_pp_revision_header(handler)
            handler.end_headers()
            handler.wfile.write(empty_payload)
            return

        # Extract scalar field for coloring
        if field_name == "speed":
            # Default: velocity magnitude
            if "U" in streams.point_data:
                scalars = np.linalg.norm(streams.point_data["U"], axis=1).astype(np.float32)
            else:
                scalars = np.zeros(streams.n_points, dtype=np.float32)
        else:
            # Use CachedPostProcessor's field resolution for arbitrary fields
            resolved, is_comp, comp_idx = cpp._resolve_field_name(field_name)
            scalars = cpp._extract_scalars_from_mesh(
                streams, resolved, is_comp, comp_idx
            )

        points = streams.points.astype(np.float32)
        smin, smax = float(scalars.min()), float(scalars.max())

        # Extract polylines from VTK lines array
        lines_arr = streams.lines
        streamlines_meta = []
        all_data = []
        idx = 0
        while idx < len(lines_arr):
            n_pts = lines_arr[idx]
            pt_indices = lines_arr[idx + 1 : idx + 1 + n_pts]
            streamlines_meta.append({"nPoints": int(n_pts)})
            for pi in pt_indices:
                all_data.extend([points[pi, 0], points[pi, 1], points[pi, 2], scalars[pi]])
            idx += 1 + n_pts

        # Pack as binary: header JSON + binary data
        header_json = json.dumps({
            "streamlines": streamlines_meta,
            "scalarRange": [smin, smax],
            "field": field_name,
        }).encode("utf-8")
        data_binary = np.array(all_data, dtype=np.float32).tobytes()

        # Pad header to 4-byte alignment so Float32Array after it is aligned
        pad_len = (4 - (len(header_json) % 4)) % 4
        header_padded = header_json + b"\x00" * pad_len
        payload = struct.pack("<I", len(header_padded)) + header_padded + data_binary

        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        _send_pp_revision_header(handler)
        handler.end_headers()
        handler.wfile.write(payload)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


@_pp_revision_required
def handle_pp_slice(handler):
    """POST /api/pp/slice — Slice internal mesh with a plane and return geometry + field."""
    cpp = _get_cached_pp()
    internal = cpp.get_internal_mesh()
    if internal is None:
        _send_json(handler, 400, {"error": "No internal mesh loaded"})
        return

    try:
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
        normal = body.get("normal", [0, 0, 1])
        origin = body.get("origin", list(internal.center))
        field_name = body.get("field", "T")

        payload, info = cpp.get_slice(
            normal=tuple(normal),
            origin=tuple(origin),
            field_name=field_name,
        )

        if not payload:
            _send_json(handler, 400, {"error": info.get("error", "Slice failed")})
            return

        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        _send_pp_revision_header(handler)
        handler.end_headers()
        handler.wfile.write(payload)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


@_pp_revision_required
def handle_pp_volume(handler):
    """POST /api/pp/volume — Resample internal mesh to 3D grid for volume rendering."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, 400, {"error": "Not initialized. Call /api/pp/init first."})
        return

    try:
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
        field_name = body.get("field", "T")
        resolution = int(body.get("resolution", 128))

        payload, info = cpp.get_volume(field_name=field_name, resolution=resolution)

        if not payload:
            _send_json(handler, 400, {"error": info.get("error", "Volume resampling failed")})
            return

        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        _send_pp_revision_header(handler)
        handler.end_headers()
        handler.wfile.write(payload)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_pp_ai_status(handler):
    """GET /api/pp/ai/status — Return persistent AI worker status."""
    try:
        status = _get_pp_ai_worker().status()
        _send_json(handler, 200, status)
    except Exception as e:
        _send_json(handler, 500, {"ready": False, "error": str(e)})


def handle_pp_ai_demo_model_path(handler):
    """GET /api/pp/ai/demo-model-path — the demo checkpoint path, if one is set.

    The page used to carry this path as a hardcoded string. It named a private
    repository and the developer's home directory, and it shipped to every
    browser that opened the editor — which is the association chain the
    anonymous-review guard exists to break. It now comes from the environment,
    so a machine without DCDTUNI_AI_DEMO_MODEL simply has no demo button
    payload rather than leaking someone else's.
    """
    path = os.environ.get("DCDTUNI_AI_DEMO_MODEL", "").strip()
    _send_json(handler, 200, {"path": path})


def handle_pp_ai_start(handler):
    """POST /api/pp/ai/start — Load fixedEmbedding model and warm up worker."""
    cpp = _get_cached_pp()
    from backend.aircooling.runner.postprocess import PostprocessRevisionError
    revision = handler.headers.get("X-PP-Revision")
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
        case_version = CASE_DIR_VERSION

    try:
        with cpp.require_revision(revision, case_version):
            body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
            model_path = str(body.get("modelPath", "")).strip()
            case_path_raw = str(body.get("casePath", "")).strip()
            if not model_path:
                _send_json(handler, 400, {"error": "modelPath is required"})
                return
            if not case_path_raw:
                _send_json(handler, 400, {"error": "casePath is required"})
                return
            requested_path = Path(case_path_raw).expanduser()
            case_path = (requested_path if requested_path.is_absolute()
                         else case_dir / requested_path).resolve()
            if cpp.run_dir.resolve() != case_path:
                _send_json(
                    handler,
                    400,
                    {"error": f"AI case path must match current postprocess run: {cpp.run_dir}"},
                )
                return

        # Worker startup can take minutes. A second tab must remain free to
        # switch frames; re-check the revision before binding the ready worker.
        response = _get_pp_ai_worker().start(model_path=model_path, case_path=str(case_path))
        with CASE_DIR_LOCK:
            current_case_version = CASE_DIR_VERSION
        with cpp.require_revision(revision, current_case_version) as identity:
            cpp.bind_ai_worker(revision)
            payload = {
                "success": True,
                "status": response.get("status", {}),
                "result": response.get("result", {}),
                "stderr": _get_pp_ai_worker().recent_stderr(),
                "predictionSource": "independent_h5_graph",
                "timeConditioned": False,
                **identity,
            }
            _send_json(handler, 200, payload)
    except PostprocessRevisionError as exc:
        _send_json(handler, exc.status, {"code": exc.code, "error": exc.code})
    except Exception as e:
        _send_json(handler, 500, {"success": False, "error": str(e)})


def handle_pp_ai_infer(handler):
    """POST /api/pp/ai/infer — Run adaptive-region AI inference and inject fields."""
    cpp = _get_cached_pp()
    from backend.aircooling.runner.postprocess import PostprocessRevisionError
    revision = handler.headers.get("X-PP-Revision")
    with CASE_DIR_LOCK:
        case_version = CASE_DIR_VERSION

    try:
        import numpy as np

        with cpp.require_revision(revision, case_version):
            cpp.require_ai_worker(revision)
            body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
            box = body.get("box")
            if not isinstance(box, dict) or "min" not in box or "max" not in box:
                _send_json(handler, 400, {"error": "box.min and box.max are required"})
                return

        # The worker is external and slow. Its result is provisional until the
        # loaded snapshot is revalidated immediately before field injection.
        response = _get_pp_ai_worker().infer(box=box)
        result = response.get("result", {})
        output_path = Path(str(result.get("outputPath", "")))
        if not output_path.exists():
            _send_json(handler, 500, {"error": f"AI output file missing: {output_path}"})
            return

        with np.load(output_path) as data:
            fields = {
                "AI_U": np.asarray(data["AI_U"], dtype=np.float32),
                "AI_p": np.asarray(data["AI_p"], dtype=np.float32),
                "AI_T": np.asarray(data["AI_T"], dtype=np.float32),
                "AI_region_mask": np.asarray(data["AI_region_mask"], dtype=np.float32),
            }

        with CASE_DIR_LOCK:
            current_case_version = CASE_DIR_VERSION
        with cpp.require_revision(revision, current_case_version):
            cpp.require_ai_worker(revision)
            available_fields = cpp.inject_internal_fields(fields)
            _send_json(
                handler,
                200,
                {
                    "success": True,
                    "result": result,
                    "fields": available_fields,
                    "stderr": _get_pp_ai_worker().recent_stderr(),
                    "predictionSource": "independent_h5_graph",
                    "timeConditioned": False,
                    **cpp.current_identity(),
                },
            )
    except PostprocessRevisionError as exc:
        _send_json(handler, exc.status, {"code": exc.code, "error": exc.code})
    except Exception as e:
        _send_json(handler, 500, {"success": False, "error": str(e)})


def _get_pyvista_lazy():
    """Import pyvista lazily, reusing the postprocess module's setup if available."""
    try:
        from backend.aircooling.runner.postprocess import _get_pyvista
        return _get_pyvista()
    except ImportError:
        import pyvista as pv
        pv.OFF_SCREEN = True
        return pv


# ---------------------------------------------------------------------------
# /api/browser/*  handlers
# ---------------------------------------------------------------------------

def handle_browser_close(handler) -> None:
    """POST /api/browser/close — authenticated, never starts a browser."""
    result = _close_browser()
    status = 200 if result["ok"] else 202 if result["status"] == "closing" else 500
    _send_json(handler, status, result)

def handle_browser_exec_js(handler) -> None:
    """POST /api/browser/exec-js  {"js": "..."}"""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    data = _read_body(handler)
    js_code = data.get("js", "")
    if not js_code:
        _send_json(handler, 400, {"error": "missing 'js' field"})
        return
    try:
        result = _browser_call("exec_js", js_code)
        _send_json(handler, 200, {"result": result})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_load_proj(handler) -> None:
    """POST /api/browser/load-proj  {"path": "..."}"""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    data = _read_body(handler)
    proj_path = data.get("path", "")
    if not proj_path:
        _send_json(handler, 400, {"error": "missing 'path' field"})
        return
    resolved = Path(proj_path).expanduser().resolve()
    if not resolved.exists():
        _send_json(handler, 404, {"error": f"File not found: {resolved}"})
        return
    try:
        result = _browser_call("load_project", str(resolved))
        _send_json(handler, 200, result)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_state(handler) -> None:
    """GET /api/browser/state"""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    try:
        result = _browser_call("get_state_summary")
        _send_json(handler, 200, result)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_screenshot(handler) -> None:
    """GET /api/browser/screenshot?name=xxx"""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    parsed = urlparse(handler.path)
    query = parse_qs(parsed.query)
    name = query.get("name", ["api_debug"])[0]
    try:
        path = _browser_call("screenshot", name)
        _send_json(handler, 200, {"path": path})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_click(handler) -> None:
    """POST /api/browser/click  {"selector": "#btn"} or {"text": "button text"}"""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    data = _read_body(handler)
    try:
        if "selector" in data:
            result = _browser_call("click", data["selector"])
        elif "text" in data:
            result = _browser_call("click_text", data["text"])
        else:
            _send_json(handler, 400, {"error": "need 'selector' or 'text'"})
            return
        _send_json(handler, 200, result)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_export_cfdrun(handler) -> None:
    """POST /api/browser/export-cfdrun (or legacy /api/browser/export-cfd alias)."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    try:
        result = _browser_call("export_cfd_json")
        _send_json(handler, 200, result)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_geometry_status(handler) -> None:
    """GET /api/browser/geometry-status — Diagnostic: check headless geometry state."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    try:
        # React keeps the scene on the builder rather than in free variables, so
        # it answers this itself; the legacy's globals are read directly. Same
        # four keys either way — a caller should not have to know which app it is
        # talking to in order to read a diagnostic.
        result = _browser_call("exec_js", """(() => {
            if (window.__dcdtuni && window.__dcdtuni.geometryStatus) {
                return window.__dcdtuni.geometryStatus();
            }
            return {
                webglAvailable: typeof webglAvailable !== 'undefined' ? webglAvailable : null,
                renderMeshesCount: typeof renderMeshes !== 'undefined' ? renderMeshes.length : -1,
                modelsLoaded: (typeof window.allModels === 'function') ? window.allModels().length : 0,
                rendererExists: typeof renderer !== 'undefined' && renderer !== null,
            };
        })()""")
        _send_json(handler, 200, result)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_export_stl(handler) -> None:
    """POST /api/browser/export-stl — Generate STL files in headless browser, write to triSurface/."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    try:
        # Ensure geometry is built.
        #
        # The legacy's buildGeometryData returns nothing and leaves the count in
        # the free variable `renderMeshes`; React's returns the count and has no
        # such global. Take whichever the app offers, so this line does not
        # decide which app it is talking to.
        _browser_call("exec_js", """(async () => {
            const __aid = (name) => {
                const ns = window.__dcdtuni;
                if (ns && typeof ns[name] === 'function') return ns[name].bind(ns);
                if (typeof window[name] === 'function') return window[name].bind(window);
                throw new Error('automation global not found: ' + name);
            };
            const n = await __aid('buildGeometryData')();
            if (typeof n === 'number') return n;
            return (typeof renderMeshes !== 'undefined') ? renderMeshes.length : -1;
        })()""")
        # Export grouped STL map
        result = _browser_call("exec_js", """(() => {
            const __aid = (name) => {
                const ns = window.__dcdtuni;
                if (ns && typeof ns[name] === 'function') return ns[name].bind(ns);
                if (typeof window[name] === 'function') return window[name].bind(window);
                throw new Error('automation global not found: ' + name);
            };
            const allIds = __aid('allModels')().map(m => m.uid);
            const groupedMap = __aid('buildGroupedStlMap')(allIds, true);
            const encoded = {};
            for (const [filename, content] of Object.entries(groupedMap)) {
                encoded[filename] = btoa(unescape(encodeURIComponent(content)));
            }
            return { stlFiles: encoded, count: Object.keys(encoded).length };
        })()""")
        # Publish the full STL batch only after decoding/staging every member.
        tri_dir = Path(case_dir) / "Mesh" / "constant" / "triSurface"
        stl_files = result.get("stlFiles", {})
        _publish_case_inputs(
            case_dir, None, None, stl_files,
            require_manifest=False, replace_stls=True,
        )
        _send_json(handler, 200, {
            "success": True,
            "filesWritten": list(stl_files.keys()),
            "count": result.get("count", 0),
            "triSurfaceDir": str(tri_dir),
        })
    except CaseExecutionBusyError as e:
        _send_json(handler, 409, {"code": "case_busy", "error": str(e)})
    except ValueError as e:
        _send_json(handler, 400, {"error": str(e)})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_export_case(handler) -> None:
    """POST /api/browser/export-case — Load cfdproj, export STL+cfdrun to case dir.

    Body: {"projPath": "/path/to/project.cfdproj"}

    Combines load-proj + export-stl + export-cfdrun into a single call.
    Writes STL files to Mesh/constant/triSurface/ and <basename>.cfdrun to model/.
    Does NOT trigger mesh generation or solver — use /api/run-mesh or
    /api/browser/full-pipeline for that.

    Typical usage from Claude Code:
        curl -b cookie.txt -X POST http://localhost:<port>/api/browser/export-case \\
            -H "Content-Type: application/json" \\
            -d '{"projPath": "/path/to/STDCT.cfdproj"}'
    """
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    data = _read_body(handler)
    proj_path = data.get("projPath")
    if not proj_path:
        _send_json(handler, 400, {"error": "projPath is required"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set. Call /api/set-case-dir first."})
        return

    try:
        with _browser_sequence():
            # Step 1: Load project into headless browser
            load_result = _browser_call("load_project", proj_path)
            if not load_result.get("success"):
                _send_json(handler, 500, {"error": f"Project load failed: {load_result.get('error')}"})
                return
            model_count = load_result.get("modelCount", 0)

            # Step 2: Export grouped STL files to triSurface/
            stl_result = _browser_call("exec_js", """(async () => {
                const __aid = (name) => {
                    const ns = window.__dcdtuni;
                    if (ns && typeof ns[name] === 'function') return ns[name].bind(ns);
                    if (typeof window[name] === 'function') return window[name].bind(window);
                    throw new Error('automation global not found: ' + name);
                };
                await __aid('buildGeometryData')();
                const allIds = __aid('allModels')().map(m => m.uid);
                const groupedMap = __aid('buildGroupedStlMap')(allIds, true);
                const encoded = {};
                for (const [filename, content] of Object.entries(groupedMap)) {
                    encoded[filename] = btoa(unescape(encodeURIComponent(content)));
                }
                return encoded;
            })()""")
            tri_dir = Path(case_dir) / "Mesh" / "constant" / "triSurface"
            stl_names = list(stl_result)

            # Step 3: Export <basename>.cfdrun to model/ (V2 schema; buildCfdrunJsonV2
            # is async — page.evaluate awaits the returned promise. The legacy
            # buildCfdExportJson alias emits a V1 manifest the runner now rejects.)
            # Same app-agnostic resolution as export_cfdrun_content above, including
            # the legacy-only world-matrix flush (the legacy buildCfdrunJsonV2 reads
            # matrixWorld without refreshing it; React's builder flushes inside).
            _flush = ("" if APP_MODE == "react"
                      else "try { wrapperGroup.updateMatrixWorld(true); } catch(e) {}")
            cfd_result = _browser_call("exec_js", """(async () => {
                %s
                const build = (window.__dcdtuni && window.__dcdtuni.buildCfdrunJsonV2)
                    ? window.__dcdtuni.buildCfdrunJsonV2.bind(window.__dcdtuni)
                    : window.buildCfdrunJsonV2;
                return JSON.stringify(await build());
            })()""" % _flush)
            cfdrun_name = _browser_call("exec_js",
                "(window.__dcdtuni && window.__dcdtuni.getCfdrunFilename)"
                " ? window.__dcdtuni.getCfdrunFilename()"
                " : window.getCfdrunFilename()")
        cfdrun_path, cfdrun_sha = _publish_case_inputs(
            case_dir, cfdrun_name, cfd_result, stl_result,
            replace_stls=True,
        )

        _send_json(handler, 200, {
            "success": True,
            "modelCount": model_count,
            "stlFiles": stl_names,
            "stlCount": len(stl_names),
            "triSurfaceDir": str(tri_dir),
            "cfdrunPath": str(cfdrun_path),
            "cfdrunSha256": cfdrun_sha,
        })
    except CaseExecutionBusyError as e:
        _send_json(handler, 409, {"code": "case_busy", "error": str(e)})
    except ValueError as e:
        _send_json(handler, 400, {"error": str(e)})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})


def handle_browser_full_pipeline(handler) -> None:
    """POST /api/browser/full-pipeline — One-shot: load proj → export STL+JSON → run mesh."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    data = _read_body(handler)
    proj_path = data.get("projPath")
    mesh_only = data.get("meshOnly", False)
    solve_only = data.get("solveOnly", False)
    if not proj_path:
        _send_json(handler, 400, {"error": "projPath is required"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return

    try:
        with _browser_sequence():
            # Step 1: Load project
            load_result = _browser_call("load_project", proj_path)
            if not load_result.get("success"):
                _send_json(handler, 500, {"error": f"Project load failed: {load_result.get('error')}"})
                return

            # Step 2: Export STL files
            stl_result = _browser_call("exec_js", """(async () => {
                const __aid = (name) => {
                    const ns = window.__dcdtuni;
                    if (ns && typeof ns[name] === 'function') return ns[name].bind(ns);
                    if (typeof window[name] === 'function') return window[name].bind(window);
                    throw new Error('automation global not found: ' + name);
                };
                await __aid('buildGeometryData')();
                const allIds = __aid('allModels')().map(m => m.uid);
                const groupedMap = __aid('buildGroupedStlMap')(allIds, true);
                const encoded = {};
                for (const [filename, content] of Object.entries(groupedMap)) {
                    encoded[filename] = btoa(unescape(encodeURIComponent(content)));
                }
                return encoded;
            })()""")
            # Step 3: Export <basename>.cfdrun
            # The browser filename must pass through _publish_case_inputs before it
            # enters subprocess argv or the JSON response. That helper sanitizes the
            # name and atomically publishes the manifest and grouped STL files.
            # V2 schema: buildCfdrunJsonV2 is async — page.evaluate awaits the promise.
            # Same app-agnostic resolution as export_cfdrun_content above, including
            # the legacy-only world-matrix flush (the legacy buildCfdrunJsonV2 reads
            # matrixWorld without refreshing it; React's builder flushes inside).
            _flush = ("" if APP_MODE == "react"
                      else "try { wrapperGroup.updateMatrixWorld(true); } catch(e) {}")
            cfd_result = _browser_call("exec_js", """(async () => {
                %s
                const build = (window.__dcdtuni && window.__dcdtuni.buildCfdrunJsonV2)
                    ? window.__dcdtuni.buildCfdrunJsonV2.bind(window.__dcdtuni)
                    : window.buildCfdrunJsonV2;
                return JSON.stringify(await build());
            })()""" % _flush)
            cfdrun_name = _browser_call("exec_js",
                "(window.__dcdtuni && window.__dcdtuni.getCfdrunFilename)"
                " ? window.__dcdtuni.getCfdrunFilename()"
                " : window.getCfdrunFilename()")
        cfdrun_path, cfdrun_sha = _publish_case_inputs(
            case_dir, cfdrun_name, cfd_result, stl_result,
            replace_stls=True,
        )

        # Step 4: Run mesh (reuse existing run_mesh logic)
        import uuid
        job_id = uuid.uuid4().hex[:8]
        with JOBS_LOCK:
            JOBS[job_id] = _new_job_state([])

        # Build argv list — NO shell=True. With the sanitizer above, cfdrun_path
        # is guaranteed to be a safe pathname, but we still avoid the shell
        # layer entirely as defense in depth (codex review 2026-04-11 #1).
        cmd_argv = [
            sys.executable, "-m", "backend.aircooling.runner.run_cfd",
            "--from-cfdrun", str(cfdrun_path),
            "--expected-cfdrun-sha256", cfdrun_sha,
        ]
        if mesh_only:
            cmd_argv.append("--mesh-only")
        elif solve_only:
            cmd_argv.append("--solve-only")

        def _run():
            proc = None
            quality = None
            try:
                _raise_if_job_cancelled(job_id)
                _release_idle_software_browser()
                proc = _spawn_job_process(
                    job_id, cmd_argv,
                    cwd=str(case_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, preexec_fn=os.setsid,
                )
                for line in proc.stdout:
                    with JOBS_LOCK:
                        JOBS[job_id]["lines"].append(line.rstrip())
                proc.wait()
            except _JobCancelledError:
                pass
            except Exception as ex:
                with JOBS_LOCK:
                    JOBS[job_id]["lines"].append(f"[ERROR] {ex}")
            finally:
                _join_job_cleanup(job_id, proc)
                rc = proc.returncode if proc is not None else -1
                with JOBS_LOCK:
                    lines = list(JOBS[job_id]["lines"])
                if not solve_only:
                    quality = _summarize_mesh_run_quality(lines, rc)
                else:
                    quality = _summarize_solve_mesh_quality_rejection(lines, rc)
                if rc == 0:
                    (Path(case_dir) / "Mesh" / "case.foam").write_text("")
                _finish_job(
                    job_id, rc, quality=quality,
                    cancelled_receipt=(Path(case_dir) / "mesh-quality-receipt.json")
                    if proc is not None and not solve_only else None,
                )

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        _send_json(handler, 200, {
            "success": True,
            "jobId": job_id,
            "stlFiles": list(stl_result.keys()),
            "modelsLoaded": load_result.get("modelCount", 0),
        })
    except CaseExecutionBusyError as e:
        _send_json(handler, 409, {"code": "case_busy", "error": str(e)})
    except ValueError as e:
        _send_json(handler, 400, {"error": str(e)})
    except Exception as e:
        import traceback
        _send_json(handler, 500, {"error": str(e), "traceback": traceback.format_exc()})


def handle_browser_logs(handler) -> None:
    """GET /api/browser/logs?clear=true"""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    parsed = urlparse(handler.path)
    query = parse_qs(parsed.query)
    clear = query.get("clear", ["false"])[0].lower() == "true"
    logs = list(_browser_console_logs)
    errors = list(_browser_errors)
    if clear:
        _browser_console_logs.clear()
        _browser_errors.clear()
    _send_json(handler, 200, {"logs": logs[-100:], "errors": errors, "totalLogs": len(logs)})


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _read_body(handler: http.server.BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length)
    return json.loads(raw) if raw else {}


def handle_render_scene_export(handler):
    """Persist an explicitly derived render frame under the selected case."""
    from render_scene_files import export_render_scene, MAX_REQUEST_BYTES
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError('render scene request exceeds 64 MiB')
        raw = handler.rfile.read(length)
        body = json.loads(raw) if raw else {}
        with CASE_DIR_LOCK:
            captured = CASE_DIR
        if captured is None:
            raise ValueError('No case directory set')
        receipt = export_render_scene(body, captured_case_dir=Path(captured))
        _send_json(handler, 201, receipt)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        _send_json(handler, 400, {'error': str(exc)})
    except OSError as exc:
        _send_json(handler, 500, {'error': str(exc)})


def handle_ov_result_dataset(handler):
    """POST /api/ov/result-dataset {runId} — convert a run of the selected case
    for the OV display bridge and return a server-signed display grant.

    The browser relays the grant to Kit verbatim; Kit verifies it with the key
    start_server.sh handed both processes, so no browser-supplied path is ever
    opened. A grant is display plumbing, not result provenance."""
    import ov_result_dataset as ovd
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if length < 0 or length > 4096:
            raise ValueError('request body too large')
        raw = handler.rfile.read(length)
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict) or set(body) != {'runId'}:
            raise ValueError('body must be exactly {"runId": <64-hex>}')
        with CASE_DIR_LOCK:
            captured = CASE_DIR
        if captured is None:
            _send_json(handler, 400, {'code': 'case_not_selected', 'error': 'No case directory set'})
            return
        key = os.environ.get('DCDTUNI_OV_GRANT_KEY', '')
        if not key:
            _send_json(handler, 503, {'code': 'ov_grant_unavailable',
                                      'error': 'Server was not started with --ov; no display grant key'})
            return
        from backend.aircooling.runner.postprocess import _foam_source_prefix
        run = ovd.find_run(Path(captured), body['runId'])
        prepared = ovd.prepare_ensight(run, ovd.foam_to_ensight(_foam_source_prefix()))
        grant = ovd.issue_grant(prepared, run, body['runId'], key)
        _send_json(handler, 200, {'grant': grant, 'run': run.name, 'time': prepared['time'],
                                  'fields': prepared['fields'], 'cached': prepared['cached'],
                                  'format': 'ensight'})
    except ovd.DatasetError as exc:
        _send_json(handler, exc.status, {'code': exc.code, 'error': exc.error})
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        _send_json(handler, 400, {'code': 'bad_request', 'error': str(exc)})
    except (OSError, RuntimeError) as exc:
        _send_json(handler, 500, {'code': 'ov_dataset_failed', 'error': str(exc)})


def handle_ov_liquid_result_grant(handler):
    """POST /api/ov/liquid-result-grant {runId} — sign a display grant for a DAE
    run of the selected case (Liquid/log/run-<id>/result.json). Kit re-hashes
    the file, binds it to the acknowledged scene and computes the colours;
    legacy results without object bindings are refused with the missing keys."""
    import ov_liquid_result as olr
    from ov_result_dataset import DatasetError
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if length < 0 or length > 4096:
            raise ValueError('request body too large')
        raw = handler.rfile.read(length)
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict) or set(body) != {'runId'}:
            raise ValueError('body must be exactly {"runId": <32-hex>}')
        with CASE_DIR_LOCK:
            captured = CASE_DIR
        if captured is None:
            _send_json(handler, 400, {'code': 'case_not_selected', 'error': 'No case directory set'})
            return
        key = os.environ.get('DCDTUNI_OV_GRANT_KEY', '')
        if not key:
            _send_json(handler, 503, {'code': 'ov_grant_unavailable',
                                      'error': 'Server was not started with --ov; no display grant key'})
            return
        run = olr.find_liquid_run(Path(captured), body['runId'])
        payload = olr.prepare_grant_payload(run, body['runId'])
        grant = olr.issue_grant(payload, key)
        _send_json(handler, 200, {'grant': grant, 'runId': body['runId'], 'status': payload['status'],
                                  'samples': payload['samples'], 'solver': payload['solver']})
    except DatasetError as exc:
        reply = {'code': exc.code, 'error': exc.error}
        if getattr(exc, 'missing', None):
            reply['missing'] = exc.missing
        _send_json(handler, exc.status, reply)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        _send_json(handler, 400, {'code': 'bad_request', 'error': str(exc)})
    except OSError as exc:
        _send_json(handler, 500, {'code': 'ov_liquid_grant_failed', 'error': str(exc)})


def _read_cfdrun_body_key(data: dict) -> str | None:
    """Read the `.cfdrun` manifest JSON string from a POST body.

    Primary key is ``cfdrunJson``; ``cfdExportJson`` is accepted for one
    submodule-bump backward-compat window and logs a deprecation warning so
    API-driving agents see the migration hint on the first call. When the
    alias is retired, delete this helper and inline ``data.get("cfdrunJson")``
    at both call sites.
    """
    if data.get("cfdrunJson") is not None:
        return data.get("cfdrunJson")
    if data.get("cfdExportJson") is not None:
        logging.getLogger(__name__).warning(
            "POST body used deprecated key 'cfdExportJson'; rename to 'cfdrunJson'. "
            "This alias will be removed in the next cleanup pass."
        )
        return data.get("cfdExportJson")
    return None


def _send_json(handler: http.server.BaseHTTPRequestHandler, status: int, data: dict) -> None:
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _send_options(handler: http.server.BaseHTTPRequestHandler) -> None:
    handler.send_response(204)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-PP-Revision")
    handler.end_headers()


# ---------------------------------------------------------------------------
# GET /api/server-info
# ---------------------------------------------------------------------------

_OV_SIGNALING_DEFAULT_PORT = 8301
_OV_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_OV_PORT = re.compile(r"[1-9][0-9]{0,4}\Z")


def _ov_signaling_host(raw: str | None) -> str | None:
    # Return only a plain ASCII DNS name or IPv4-style host. Never echo a URL,
    # path, port, user-info, whitespace or control character into browser config.
    if raw is None or not raw.strip():
        return None
    if raw != raw.strip() or len(raw) > 253:
        return None
    return raw if all(_OV_HOST_LABEL.fullmatch(label) for label in raw.split(".")) else None


def _ov_signaling_port(raw: str | None) -> int:
    if raw is not None and _OV_PORT.fullmatch(raw):
        port = int(raw)
        if port <= 65535:
            return port
    return _OV_SIGNALING_DEFAULT_PORT


def handle_server_info(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Return server configuration and capability status."""
    import os
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    case_dir_ok = cd is not None and cd.is_dir()
    _send_json(handler, 200, {
        "dctwinAvailable": DCTWIN_AVAILABLE,
        "dctwinVersion": DCTWIN_VERSION,
        "pythonExecutable": sys.executable,
        "caseDir": str(cd) if cd else None,
        "caseDirOk": case_dir_ok,
        "allOk": DCTWIN_AVAILABLE and case_dir_ok,
        "cpuCount": os.cpu_count() or 1,
        "wsPort": WS_PORT,
        "ovSignalingHost": _ov_signaling_host(os.environ.get("DCDTUNI_OV_SIGNALING_HOST")),
        "ovSignalingPort": _ov_signaling_port(os.environ.get("DCDTUNI_OV_SIGNALING_PORT")),
        # start_server.sh --ov is the only launcher that sets the grant key and
        # starts Kit; without it, choosing Omniverse can only time out.
        "ovBridge": bool(os.environ.get("DCDTUNI_OV_GRANT_KEY")),
        "authRequired": AUTH_MGR is not None,
    })


# ---------------------------------------------------------------------------
# GET /api/browse-dir — list directories for the file browser dialog
# ---------------------------------------------------------------------------

def handle_browse_dir(handler: http.server.BaseHTTPRequestHandler, query: dict) -> None:
    """List subdirectories and matching files of a given path for the browser-side file picker."""
    raw = (query.get("path", [""])[0]).strip()
    extensions_raw = (query.get("extensions", [""])[0]).strip()
    # Default to home directory if no path given
    if not raw:
        raw = str(Path.home())
    p = Path(raw).expanduser().resolve()

    if not p.exists():
        _send_json(handler, 400, {"error": f"Path does not exist: {p}"})
        return
    if not p.is_dir():
        _send_json(handler, 400, {"error": f"Not a directory: {p}"})
        return

    # Parse file extensions filter (comma-separated, e.g. ".cfdproj,.json")
    exts = set()
    if extensions_raw:
        for ext in extensions_raw.split(","):
            ext = ext.strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext:
                exts.add(ext.lower())

    dirs = []
    files = []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0
            if entry.is_dir():
                dirs.append({
                    "name": entry.name,
                    "path": str(entry),
                    "hidden": entry.name.startswith("."),
                    "mtime": mtime,
                })
            elif entry.is_file() and (not exts or entry.suffix.lower() in exts):
                files.append({
                    "name": entry.name,
                    "path": str(entry),
                    "hidden": entry.name.startswith("."),
                    "mtime": mtime,
                    "size": entry.stat().st_size,
                })
    except PermissionError:
        _send_json(handler, 403, {"error": f"Permission denied: {p}"})
        return

    parent = str(p.parent) if p.parent != p else None
    _send_json(handler, 200, {
        "current": str(p),
        "parent": parent,
        "dirs": dirs,
        "files": files,
    })


# ---------------------------------------------------------------------------
# GET /api/read-file — read a file's text content (for loading remote .cfdproj)
# ---------------------------------------------------------------------------

def handle_read_file_binary(handler: http.server.BaseHTTPRequestHandler, query: dict) -> None:
    """GET /api/read-file-binary — a file as base64 + MIME, for images the UI
    turns into textures. read-file is UTF-8 text only and 500s on a JPEG."""
    import base64
    import mimetypes

    raw = (query.get("path", [""])[0]).strip()
    if not raw:
        _send_json(handler, 400, {"ok": False, "error": "Missing 'path' parameter"})
        return
    p = Path(raw).expanduser().resolve()
    if not p.is_file():
        _send_json(handler, 404, {"ok": False, "error": f"File not found: {p}"})
        return
    limit = 16 * 1024 * 1024
    if p.stat().st_size > limit:
        _send_json(handler, 413, {"ok": False, "error": f"File larger than {limit} bytes: {p}"})
        return
    try:
        data = p.read_bytes()
    except PermissionError:
        _send_json(handler, 403, {"ok": False, "error": f"Permission denied: {p}"})
        return
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    _send_json(handler, 200, {
        "ok": True,
        "name": p.name,
        "mime": mime,
        "size": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    })


def handle_read_file(handler: http.server.BaseHTTPRequestHandler, query: dict) -> None:
    """Read a file from the server filesystem and return its text content."""
    raw = (query.get("path", [""])[0]).strip()
    if not raw:
        _send_json(handler, 400, {"ok": False, "error": "Missing 'path' parameter"})
        return
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        _send_json(handler, 404, {"ok": False, "error": f"File not found: {p}"})
        return
    if not p.is_file():
        _send_json(handler, 400, {"ok": False, "error": f"Not a file: {p}"})
        return
    try:
        content = p.read_text(encoding="utf-8")
        _send_json(handler, 200, {"ok": True, "content": content})
    except PermissionError:
        _send_json(handler, 403, {"ok": False, "error": f"Permission denied: {p}"})
    except Exception as e:
        _send_json(handler, 500, {"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# POST /api/write-file — write a text file to the server filesystem
# ---------------------------------------------------------------------------

def _case_for_protected_input_path(path: Path) -> Path | None:
    """Find any case input or result a generic file-write could mutate.

    The solver copies Mesh/ fields and dictionaries as well as polyMesh, then
    writes under log/. Locking just manifest/STL would let write-file change
    those bytes while a run holds the per-case lock.
    """
    selected = Path(CASE_DIR).resolve() if CASE_DIR is not None else None

    def is_case(root: Path) -> bool:
        return root == selected or ((root / "model").is_dir() and (root / "Mesh").is_dir())

    for ancestor in path.parents:
        if ancestor.name in {"Mesh", "log", "model"} and is_case(ancestor.parent):
            if ancestor.name == "model" and path.suffix.lower() == ".cfdproj":
                return None
            return ancestor.parent
        if is_case(ancestor):
            # Root-level heat ledgers, source/quality receipts and generated
            # derived manifests also participate in the solve contract.
            return ancestor
    return None


def handle_write_file(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Write a text file to the server filesystem with optional overwrite protection."""
    data = _read_body(handler)
    path_str = data.get("path", "").strip()
    content = data.get("content", "")
    overwrite = bool(data.get("overwrite", False))

    if not path_str:
        _send_json(handler, 400, {"ok": False, "error": "path is required"})
        return

    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)

    p = Path(path_str).expanduser().resolve()
    protected_case = _case_for_protected_input_path(p)
    try:
        with (case_execution_lock(protected_case) if protected_case else nullcontext()):
            overwrote = False
            if p.exists():
                if p.is_dir():
                    _send_json(handler, 400, {"ok": False, "error": f"Target path is a directory: {p}"})
                    return
                if not overwrite:
                    _send_json(handler, 409, {
                        "ok": False,
                        "exists": True,
                        "path": str(p),
                        "error": f"File already exists: {p}",
                    })
                    return
                overwrote = True
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    except CaseExecutionBusyError as exc:
        _send_json(handler, 409, {"ok": False, "code": "case_busy", "error": str(exc)})
        return
    except PermissionError:
        _send_json(handler, 403, {"ok": False, "error": f"Permission denied: {p}"})
        return
    except Exception as exc:
        _send_json(handler, 400, {"ok": False, "error": f"Cannot write file: {exc}"})
        return

    _send_json(handler, 200, {
        "ok": True,
        "path": str(p),
        "overwrote": overwrote,
    })


# ---------------------------------------------------------------------------
# POST /api/create-dir — create a directory (for the folder browser "New Folder")
# ---------------------------------------------------------------------------

def handle_create_dir(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Create a directory on the server (used by the folder browser dialog)."""
    data = _read_body(handler)
    path_str = data.get("path", "").strip()
    if not path_str:
        _send_json(handler, 400, {"error": "path is required"})
        return
    p = Path(path_str).expanduser().resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _send_json(handler, 403, {"error": f"Permission denied: {p}"})
        return
    except Exception as exc:
        _send_json(handler, 400, {"error": f"Cannot create directory: {exc}"})
        return
    _send_json(handler, 200, {"path": str(p), "ok": True})


# ---------------------------------------------------------------------------
# POST /api/set-case-dir
# ---------------------------------------------------------------------------

def handle_set_case_dir(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Set the case directory; automatic project restore only accepts existing paths."""
    global CASE_DIR, CASE_DIR_VERSION
    data = _read_body(handler)
    path_str = data.get("caseDir", "").strip()
    if not path_str:
        _send_json(handler, 400, {"error": "caseDir is required"})
        return
    try:
        p = Path(path_str).expanduser().resolve()
        if data.get("createIfMissing", True) is False:
            import stat
            try:
                mode = p.stat().st_mode
            except FileNotFoundError:
                _send_json(handler, 404, {"ok": False, "code": "case_dir_missing", "error": f"Directory does not exist: {p}"})
                return
            except NotADirectoryError:
                _send_json(handler, 400, {"ok": False, "code": "case_dir_not_directory", "error": f"Not a directory: {p}"})
                return
            if not stat.S_ISDIR(mode):
                _send_json(handler, 400, {"ok": False, "code": "case_dir_not_directory", "error": f"Not a directory: {p}"})
                return
        else:
            p.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        _send_json(handler, 403, {"ok": False, "code": "case_dir_permission_denied", "error": str(exc)})
        return
    except Exception as exc:
        _send_json(handler, 400, {"ok": False, "code": "case_dir_unavailable", "error": f"Cannot access directory: {exc}"})
        return
    with CASE_DIR_LOCK:
        if CASE_DIR != p:
            CASE_DIR = p
            CASE_DIR_VERSION += 1
            _get_cached_pp().invalidate()
    print(f"[server] Case dir set to: {p}")
    _send_json(handler, 200, {"caseDir": str(p), "ok": True})


# ---------------------------------------------------------------------------
# POST /api/prepare-case — save STL+JSON files, return the command to run
# ---------------------------------------------------------------------------

def handle_prepare_case(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Save STL files and <basename>.cfdrun, return the dcdtuni command string.

    The browser then sends this command to the terminal WebSocket (PTY),
    so it runs in the interactive shell where Ctrl+C can interrupt it.
    """
    data = _read_body(handler)
    cfdrun_json = _read_cfdrun_body_key(data)
    cfdrun_filename: str = data.get("cfdrunFilename", "project.cfdrun") or "project.cfdrun"
    stl_files: dict = data.get("stlFiles", {})
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)

    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": f"dcdtuni not available: {DCTWIN_VERSION}"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    try:
        cfdrun_path, cfdrun_sha = _publish_case_inputs(
            case_dir, cfdrun_filename, cfdrun_json, stl_files,
        )
    except CaseExecutionBusyError as exc:
        _send_json(handler, 409, {"code": "case_busy", "error": str(exc)})
        return
    except ValueError as exc:
        _send_json(handler, 400, {"error": str(exc)})
        return
    except OSError as exc:
        _send_json(handler, 500, {"error": f"Case upload failed: {exc}"})
        return

    # Build the command string (NOT executed — sent to terminal)
    flags = ""
    if mesh_only:
        flags = " --mesh-only"
    elif solve_only:
        flags = " --solve-only"

    cmd = ("python -m backend.aircooling.runner.run_cfd "
           f"--from-cfdrun {shlex.quote(str(cfdrun_path))} "
           f"--expected-cfdrun-sha256 {cfdrun_sha}{flags}")
    _send_json(handler, 200, {"command": cmd, "caseDir": str(case_dir),
                              "cfdrunSha256": cfdrun_sha})


# ---------------------------------------------------------------------------
# POST /api/run-mesh  (legacy — kept for backward compat)
# ---------------------------------------------------------------------------

def handle_run_mesh(handler: http.server.BaseHTTPRequestHandler) -> None:
    data = _read_body(handler)
    cfdrun_json = _read_cfdrun_body_key(data)
    cfdrun_filename: str = data.get("cfdrunFilename", "project.cfdrun") or "project.cfdrun"
    stl_files: dict = data.get("stlFiles", {})   # {filename: base64_content}
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)

    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": f"dcdtuni not available: {DCTWIN_VERSION}"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set. Use --case-dir on startup or POST /api/set-case-dir first."})
        return
    try:
        cfdrun_path, cfdrun_sha = _publish_case_inputs(
            case_dir, cfdrun_filename, cfdrun_json, stl_files,
        )
    except CaseExecutionBusyError as exc:
        _send_json(handler, 409, {"code": "case_busy", "error": str(exc)})
        return
    except ValueError as exc:
        _send_json(handler, 400, {"error": str(exc)})
        return
    except OSError as exc:
        _send_json(handler, 500, {"error": f"Case upload failed: {exc}"})
        return

    # Use THIS Python interpreter — already the correct dcdtuni env
    flags = []
    if mesh_only:
        flags.append("--mesh-only")
    elif solve_only:
        flags.append("--solve-only")

    # Always use --from-cfdrun: this server is the browser backend and the case
    # is always set up via the stlgen pipeline (browser-exported STLs).
    # For solve-only, STL files aren't re-uploaded but the mesh was generated
    # by STLGenRunner, so we must stay on the same pipeline.
    cmd = [
        sys.executable, "-m", "backend.aircooling.runner.run_cfd",
        "--from-cfdrun", str(cfdrun_path),
        "--expected-cfdrun-sha256", cfdrun_sha,
    ] + flags

    job_id = str(uuid.uuid4())[:8]
    cmd_str = " ".join(cmd)
    import datetime as _dt
    _job_started = _dt.datetime.now().strftime("%Y-%m-%d-%H-%M")
    with JOBS_LOCK:
        JOBS[job_id] = _new_job_state([st("server.start_command", cmd=cmd_str)], startedAt=_job_started)
    global _LATEST_SOLVE_PREFIX, _LATEST_SOLVE_RUN_DIR, _LATEST_SOLVE_STARTED
    if not mesh_only:
        _LATEST_SOLVE_PREFIX = _job_started
        _LATEST_SOLVE_STARTED = _dt.datetime.now()
        _LATEST_SOLVE_RUN_DIR = None

    def _run() -> None:
        proc = None
        try:
            _raise_if_job_cancelled(job_id)
            release = _release_idle_software_browser()
            with JOBS_LOCK:
                JOBS[job_id]["lines"].append(f"[server] automation browser before CFD: {release['status']}")
            proc = _spawn_job_process(
                job_id, cmd,
                cwd=str(case_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                preexec_fn=os.setsid,  # Create process group for killpg
            )
            for line in iter(proc.stdout.readline, ""):
                stripped = line.rstrip()
                if stripped:
                    with JOBS_LOCK:
                        JOBS[job_id]["lines"].append(stripped)
                    if not mesh_only:
                        _capture_solve_run_dir(stripped)
            proc.wait()
        except _JobCancelledError:
            pass
        except Exception as exc:
            with JOBS_LOCK:
                JOBS[job_id]["lines"].append(st("server.start_failed", error=exc))
                JOBS[job_id]["lines"].append(st("server.command", cmd=cmd_str))
                JOBS[job_id]["lines"].append(st("server.working_dir", dir=case_dir))
        finally:
            _join_job_cleanup(job_id, proc)
            rc = proc.returncode if proc else -1
            quality = None
            if not solve_only:
                with JOBS_LOCK:
                    quality_lines = list(JOBS[job_id]["lines"])
                quality = _summarize_mesh_run_quality(quality_lines, rc)
            if solve_only:
                with JOBS_LOCK:
                    quality_lines = list(JOBS[job_id]["lines"])
                quality = _summarize_solve_mesh_quality_rejection(quality_lines, rc)
            if rc == 0:
                # Write case.foam marker so ParaView can open the Mesh directory
                foam_file = case_dir / "Mesh" / "case.foam"
                if not foam_file.exists():
                    try:
                        foam_file.touch()
                    except Exception:
                        pass
                with JOBS_LOCK:
                    JOBS[job_id]["lines"].append(f"[server] case.foam written to {foam_file}")
            _finish_job(
                job_id, rc, quality=quality,
                cancelled_receipt=(case_dir / "mesh-quality-receipt.json")
                if proc is not None and not solve_only else None,
            )

    threading.Thread(target=_run, daemon=True).start()
    _send_json(handler, 200, {"jobId": job_id})


# ---------------------------------------------------------------------------
# POST /api/screenshot  — save a screenshot from the browser for debugging
# ---------------------------------------------------------------------------

def handle_screenshot(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Save a base64-encoded screenshot from the browser to a file."""
    data = _read_body(handler)
    image_data = data.get("imageData", "")  # data:image/png;base64,...
    if not image_data:
        _send_json(handler, 400, {"error": "missing imageData"})
        return
    # Strip data URL prefix
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    screenshot_dir = CASE_DIR / "screenshots" if CASE_DIR else Path("screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = screenshot_dir / f"ui_screenshot_{timestamp}.png"
    filepath.write_bytes(base64.b64decode(image_data))
    _send_json(handler, 200, {"path": str(filepath), "timestamp": timestamp})


# ---------------------------------------------------------------------------
# POST /api/abort-job  — kill the running subprocess for a job
# ---------------------------------------------------------------------------

def handle_abort_job(handler: http.server.BaseHTTPRequestHandler) -> None:
    data = _read_body(handler)
    job_id = data.get("jobId")
    terminate = False
    with JOBS_LOCK:
        job = JOBS.get(job_id) if job_id else None
        if job is None:
            reply = None
        elif job["done"]:
            # A late request cannot turn a natural completion into cancellation.
            reply = {
                "status": "aborted" if job.get("cancelled") else "already_done",
                "jobId": job_id, "done": True,
                "cancelRequested": bool(job.get("cancelRequested")),
                "cancelled": bool(job.get("cancelled")),
            }
        else:
            if not job.get("cancelRequested"):
                job["cancelRequested"] = True
                job["checkMeshOk"] = False
                job["lines"].append(st("server.user_aborted"))
            proc = job.get("proc")
            if proc is not None and not job.get("_stop_started"):
                job["_stop_started"] = True
                # Acquire before releasing JOBS_LOCK so the worker cannot publish
                # done between accepting cancellation and starting tree cleanup.
                terminate = job["_stop_lock"].acquire(blocking=False)
            reply = None
    if job is None:
        _send_json(handler, 404, {"error": "job not found"})
        return
    if terminate:
        stop_error = None
        try:
            if _terminate_job_tree(proc) is False:
                stop_error = "Job process tree has not stopped; cancellation remains pending."
        except Exception as exc:
            stop_error = f"Job process tree could not be stopped: {exc}"
        finally:
            job["_stop_lock"].release()
        if stop_error is not None:
            with JOBS_LOCK:
                job["_stop_started"] = False
                job["lines"].append(f"[server] {stop_error}")
            _send_json(handler, 500, {
                "error": stop_error, "jobId": job_id, "done": False,
                "cancelRequested": True, "cancelled": False,
            })
            return
    if reply is None:
        with JOBS_LOCK:
            done = job["done"]
            reply = {
                "status": "aborted" if done else "aborting",
                "jobId": job_id, "done": done,
                "cancelRequested": True,
                "cancelled": bool(job.get("cancelled")),
            }
    _send_json(handler, 200, reply)


def _descendant_pgids(root_pid: int) -> set[int]:
    """Every process group under `root_pid`, the root's own included.

    The runner starts each mpirun step with `start_new_session=True`, so the
    solver ranks sit in a session of their own and a killpg on the runner's
    group never reaches them: aborting a mesh used to leave `mpirun -np 8
    dcCartMesh` running to completion on eight cores while the UI said
    "aborted" (STDCT, 2026-09-22). /proc's children lists find them.
    """
    pgids: set[int] = set()
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            pgids.add(os.getpgid(pid))
        except (ProcessLookupError, PermissionError):
            continue
        task_dir = Path("/proc") / str(pid) / "task"
        try:
            for tid in task_dir.iterdir():
                try:
                    stack.extend(int(c) for c in (tid / "children").read_text().split())
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
    return pgids


def _terminate_job_tree(proc: subprocess.Popen, grace: float = 8.0) -> bool:
    """SIGTERM every process group of the job, wait, SIGKILL what is left.

    SIGTERM first and a real wait, not SIGTERM-then-SIGKILL in the same
    breath: the runner terminates its own child group on SIGTERM, and a
    SIGKILL landing before it can do so is exactly what orphaned the ranks.
    """
    import signal
    import time as _time

    # Retain known sessions if the launcher exits and children are reparented.
    pgids = set(getattr(proc, "_dcdtuni_job_pgids", ()))
    pgids.update(_descendant_pgids(proc.pid))
    proc._dcdtuni_job_pgids = pgids
    if proc.poll() is not None and not _any_alive(pgids):
        return True
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        if proc.poll() is None:
            pgids.update(_descendant_pgids(proc.pid))
        for pgid in pgids:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = _time.monotonic() + wait
        while _time.monotonic() < deadline:
            if proc.poll() is not None and not _any_alive(pgids):
                return True
            _time.sleep(0.1)
    return proc.poll() is not None and not _any_alive(pgids)


def _any_alive(pgids: set[int]) -> bool:
    existing = set()
    for pgid in pgids:
        try:
            os.killpg(pgid, 0)
            existing.add(pgid)
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
    if not existing:
        return False
    if not sys.platform.startswith("linux"):
        return True
    # killpg(..., 0) also sees zombie ranks. They cannot compute or retain a
    # stdout pipe/case flock, and waiting for their unrelated reaper must not
    # permanently freeze a completed cancellation.
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                tail = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                state, group = tail[0], int(tail[2])
                if group in existing and state not in ("Z", "X"):
                    return True
            except FileNotFoundError:
                continue
            except (OSError, ValueError, IndexError):
                return True
    except OSError:
        return True
    return False


# ---------------------------------------------------------------------------
# GET /api/log-stream?job_id=xxx  (Server-Sent Events)
# ---------------------------------------------------------------------------

def handle_log_stream(handler: http.server.BaseHTTPRequestHandler, query: dict) -> None:
    job_id = query.get("job_id", [None])[0]
    if not job_id:
        _send_json(handler, 400, {"error": "missing job_id"})
        return
    with JOBS_LOCK:
        job_exists = job_id in JOBS
    if not job_exists:
        _send_json(handler, 404, {"error": "job not found"})
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    sent = 0
    try:
        while True:
            with JOBS_LOCK:
                lines = JOBS[job_id]["lines"]
                done = JOBS[job_id]["done"]
                returncode = JOBS[job_id]["returncode"]
                check_mesh_ok = JOBS[job_id].get("checkMeshOk")
                failed_checks = JOBS[job_id].get("failedChecks")
                mesh_warnings = JOBS[job_id].get("meshWarnings")
                cancel_requested = bool(JOBS[job_id].get("cancelRequested"))
                cancelled = bool(JOBS[job_id].get("cancelled"))

            while sent < len(lines):
                msg = f"data: {json.dumps(lines[sent])}\n\n"
                handler.wfile.write(msg.encode())
                handler.wfile.flush()
                sent += 1

            if done and sent >= len(lines):
                final_payload = {'done': True, 'returncode': returncode, 'checkMeshOk': check_mesh_ok, 'failedChecks': failed_checks, 'cancelRequested': cancel_requested, 'cancelled': cancelled}
                if mesh_warnings is not None:
                    final_payload['meshWarnings'] = mesh_warnings
                final = f"data: {json.dumps(final_payload)}\n\n"
                handler.wfile.write(final.encode())
                handler.wfile.flush()
                break

            time.sleep(0.1)
    except (BrokenPipeError, ConnectionResetError):
        pass


# ---------------------------------------------------------------------------
# GET /api/job-status?job_id=xxx
# ---------------------------------------------------------------------------

def handle_job_status(handler: http.server.BaseHTTPRequestHandler, query: dict) -> None:
    job_id = query.get("job_id", [None])[0]
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        status = None if job is None else {
            "jobId": job_id,
            "done": job["done"],
            "returncode": job["returncode"],
            "lineCount": len(job["lines"]),
            "checkMeshOk": job.get("checkMeshOk"),
            "failedChecks": job.get("failedChecks"),
            "meshWarnings": job.get("meshWarnings"),
            "cancelRequested": bool(job.get("cancelRequested")),
            "cancelled": bool(job.get("cancelled")),
        }
    if status is None:
        _send_json(handler, 404, {"error": "job not found"})
        return
    _send_json(handler, 200, status)


# ---------------------------------------------------------------------------
# Liquid cooling API handlers (ODIL-based, 2026-05)
# ---------------------------------------------------------------------------

def handle_liquid_export_liquidrun(handler):
    """POST /api/liquid/export-liquidrun — Save .liquidrun JSON from frontend."""
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set"})
        return

    try:
        data = _read_body(handler)
        model_dir = case_dir / "Liquid" / "model"
        model_dir.mkdir(parents=True, exist_ok=True)

        project_name = data.get("projectName", "unnamed")
        out_path = model_dir / f"{project_name}.liquidrun"
        out_path.write_text(json.dumps(data, indent=2))

        _send_json(handler, 200, {"saved": str(out_path), "projectName": project_name})
    except Exception as e:
        import traceback
        traceback.print_exc()
        _send_json(handler, 500, {"error": str(e)})


def handle_liquid_solve(handler):
    """POST /api/liquid/solve — Run ODIL liquid cooling solver, streams SSE."""
    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": "dcdtuni not available"})
        return
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set"})
        return

    liquid_dir = case_dir / "Liquid"
    model_dir = liquid_dir / "model"

    liquidrun_files = sorted(model_dir.glob("*.liquidrun"))
    if not liquidrun_files:
        _send_json(handler, 404, {"error": "No .liquidrun file found in " + str(model_dir)})
        return

    liquidrun_path = liquidrun_files[-1]

    try:
        body = _read_body(handler) if handler.headers.get("Content-Length") else {}
    except Exception:
        body = {}
    device = (body or {}).get("device")
    monitor_cps = (body or {}).get("monitor_cps", [])

    # Send SSE headers
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    _client_disconnected = [False]

    def _send_sse(payload: dict):
        if _client_disconnected[0]:
            return
        try:
            msg = f"data: {json.dumps(payload)}\n\n"
            handler.wfile.write(msg.encode())
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            _client_disconnected[0] = True

    _send_sse({"stage": "compiling"})

    try:
        import torch
        torch.set_default_dtype(torch.float64)

        from backend.liquidcooling.runner import run_liquid_solve

        def _sse_callback(log_dict):
            if _client_disconnected[0]:
                raise ConnectionAbortedError("Client disconnected")
            _send_sse(log_dict)

        result = run_liquid_solve(
            liquidrun_path,
            case_dir=liquid_dir,
            device=device,
            callback=_sse_callback,
            monitor_cp_ids=monitor_cps if monitor_cps else None,
        )
        _send_sse({
            "stage": "done",
            "converged": result.converged,
            "final_loss": result.final_loss,
            "epochs": result.epochs,
        })
    except ConnectionAbortedError:
        log.info("Liquid solve aborted: client disconnected")
    except Exception as e:
        import traceback
        _send_sse({
            "stage": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        })


def handle_run_coupled_sim(handler):
    """POST /api/run-coupled-sim — Run liquid solve then air CFD."""
    data = _read_body(handler)
    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": "dcdtuni not available"})
        return
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set"})
        return

    model_dir = case_dir / "model"
    cfdrun_candidates = sorted(model_dir.glob("*.cfdrun"))
    if not cfdrun_candidates:
        _send_json(handler, 400, {"error": "No .cfdrun file found in case/model/ — run mesh first"})
        return
    if len(cfdrun_candidates) > 1:
        # Codex review 2026-04-11 #2: the write path now sweeps stale siblings,
        # so this should never happen in practice. If it does, something wrote
        # a manifest outside our sanctioned helper — fail loudly instead of
        # silently picking one alphabetically.
        names = ", ".join(p.name for p in cfdrun_candidates)
        _send_json(handler, 409, {
            "error": (
                f"Multiple .cfdrun manifests in case/model/ ({names}). "
                "Re-run mesh/prepare to regenerate a single active manifest."
            )
        })
        return
    cfd_json_path = cfdrun_candidates[0]

    try:
        import json
        cfd_export = json.loads(cfd_json_path.read_text(encoding="utf-8"))

        # Step 1: Liquid cooling solve (saves liquid_results.json)
        lc_data = cfd_export.get("liquidCooling", {})
        if lc_data.get("enabled", False):
            from backend.liquidcooling.runner import run_liquid_solve
            liquid_result = run_liquid_solve(cfd_export, case_dir=case_dir)
            print(f"[server] Liquid solve done: {len(liquid_result.servers)} servers")
        else:
            print("[server] No liquid cooling enabled, skipping liquid solve")

        # Step 2: Trigger existing mesh+solve pipeline
        # Re-use the same logic as handle_run_mesh but always do full sim
        # The stlgen_runner will automatically pick up liquid_results.json
        _send_json(handler, 200, {
            "status": "liquid_solve_complete",
            "message": "Liquid cooling solved. Proceed with mesh+solve via /api/run-mesh.",
            "liquidCoolingEnabled": lc_data.get("enabled", False),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        _send_json(handler, 500, {"error": str(e)})


def handle_liquid_results(handler):
    """GET /api/liquid/results[?run=<dir-name>] — Return ODIL liquid cooling results.

    Without ``?run=``, returns the most recent result.  With ``?run=``, looks
    for a matching directory name under the candidate log directories.
    """
    from urllib.parse import urlparse, parse_qs

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set"})
        return

    qs = parse_qs(urlparse(handler.path).query)
    requested_run = qs.get("run", [None])[0]

    result_path = selected_liquid_result(case_dir, requested_run)

    if result_path is None:
        _send_json(handler, 404, {"error": "No liquid results found"})
        return

    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        _send_json(handler, 200, data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _send_json(handler, 500, {"error": str(e)})


# TODO: migrate to ODIL transient — the old backend.liquidcooling.results and
# backend.liquidcooling.solver.transient modules were removed in the 2026-05 ODIL
# rewrite.  The handler below is preserved for reference but must NOT be
# called because the imports it depends on no longer exist.
#
# def handle_liquid_transient(handler):
#     """POST /api/liquid/transient — Run transient thermal simulation."""
#     ...  (old implementation referencing deleted modules)
#
# Once an ODIL-based transient solver is available, re-enable and update
# this handler.

def handle_liquid_transient(handler):
    """POST /api/liquid/transient — Not yet migrated to ODIL solver."""
    _send_json(handler, 501, {
        "error": (
            "POST /api/liquid/transient has not yet been migrated to the ODIL "
            "solver.  The previous implementation relied on "
            "backend.liquidcooling.results and backend.liquidcooling.solver.transient which "
            "were removed in the 2026-05 rewrite.  TODO: migrate to ODIL transient."
        )
    })


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class STLServerHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Disable browser caching for all responses (dev mode)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        path = args[0] if args else ""
        if "log-stream" not in str(path):
            super().log_message(fmt, *args)

    def list_directory(self, path):
        # Never render a directory listing. The base implementation emits an
        # HTML directory index which (a) looks like the app is broken,
        # (b) exposes web/ internals (js/, vendor/, textures/…). The only
        # legitimate directory fetch is "/" which `do_GET` already redirects
        # to the app root before reaching this method.
        self.send_error(404, "Not found")
        return None

    def do_OPTIONS(self):
        _send_options(self)

    # --- Cached headless HTML (CDN scripts inlined) ---
    def _serve_react_index(self):
        """Send `react/dist/index.html`.

        Read and sent rather than delegated to the base handler, because the
        base handler would resolve `/index.html` under HTML_DIR (the CWD) and
        the React build is a sibling of it.

        A missing build is answered with an instruction, not a 404: the failure
        the operator will actually hit is "I edited the React source and forgot
        to rebuild", and a bare 404 sends them looking at routing instead.
        """
        index = REACT_DIST / "index.html"
        if not index.is_file():
            body = (
                "React build missing: " + str(index) + "\n\n"
                "Build it, then reload:\n"
                "    cd src/frontend/react && npm run build\n\n"
                "Or run the dev server instead:\n"
                "    ./src/start_server.sh --app react --dev\n"
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The bundle filenames are content-hashed, so they may be cached hard;
        # index.html names them and must not be, or a rebuild serves the old
        # page pointing at bundles that no longer exist.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def translate_path(self, path):
        """Map a URL onto a file, across the two roots this server serves.

        The process CWD is HTML_DIR, so `SimpleHTTPRequestHandler` resolves
        everything under `web/` by default — which is right for the assets both
        apps share (`js/`, `i18n/`, `vendor/`, `textures/`, `theme/`) and wrong
        for the React build, which lives in a sibling directory.

        Only `/assets/*` is redirected: Vite emits every hashed bundle under
        that one prefix, and `web/` has no directory by that name, so the two
        namespaces cannot collide. `/` and `/index.html` are handled in
        `do_GET` because they are served, not redirected.

        Traversal is checked explicitly rather than trusted: the base
        implementation sanitises against ITS root, and a path escaping into the
        repo from here would not be caught by it.
        """
        url_path = urlparse(path).path
        if APP_MODE == "html":
            # The archived page, its 2D editor and its theme. Everything else it
            # loads (js/, vendor/, i18n/, textures/) falls through to SHARED_DIR
            # below, which is where both editors have always read them from.
            if url_path in LEGACY_FILES or url_path.startswith("/theme/"):
                target = (LEGACY_DIR / url_path.lstrip("/")).resolve()
                root = LEGACY_DIR.resolve()
                if target == root or root in target.parents:
                    return str(target)
                return str(root / "__forbidden__")
        if APP_MODE == "react":
            if url_path.startswith("/assets/"):
                rel = url_path[len("/assets/"):]
                target = (REACT_DIST / "assets" / rel).resolve()
                root = (REACT_DIST / "assets").resolve()
                if target == root or root in target.parents:
                    return str(target)
                # Escaped the assets dir — hand back a path that cannot exist so
                # the base handler answers 404, rather than serving the file.
                return str(root / "__forbidden__")
        return super().translate_path(path)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # Root path: serve the app. SimpleHTTPRequestHandler's default would
        # render a directory listing of web/, which (a) looks like the server is
        # broken, (b) exposes internal paths (js/, vendor/, etc.) even once
        # authenticated.
        #
        # React is SERVED here, not redirected to /index.html: a redirect makes
        # the browser's address bar and every screenshot filename carry a path
        # the user never typed, and the parity harness compares by URL.
        if parsed.path == "/" or parsed.path == "":
            if APP_MODE == "html":
                # Redirected, not served in place: the legacy page is addressed
                # by name everywhere — its own docs, the old skills, every
                # bookmark — so `/` and `/STL_generate.html` must end up at the
                # same URL rather than at two.
                self.send_response(302)
                self.send_header("Location", "/STL_generate.html")
                self.end_headers()
                return
            self.path = "/index.html"
            self._serve_react_index()
            return

        if parsed.path == "/index.html":
            self._serve_react_index()
            return

        # --- Public paths ---
        if parsed.path == "/login":
            _serve_login_page(self)
            return
        if parsed.path == "/api/auth/check":
            handle_auth_check(self)
            return
        if parsed.path == "/favicon.ico":
            super().do_GET()
            return
        # Loopback bypass for the internal headless browser worker: it spawns from
        # this same process and cannot carry the admin auth cookie. Full GET
        # bypass for loopback is necessary because the frontend's global fetch
        # interceptor redirects to /login on ANY 401, which would wipe the
        # script context. State-mutating POSTs still go through normal auth.
        if _get_client_ip(self) in ("127.0.0.1", "::1"):
            if (parsed.path.startswith("/api/vendor/")
                or parsed.path.startswith("/api/theme/")
                or parsed.path.startswith("/api/textures/")
                or parsed.path.startswith("/api/js/")
                or parsed.path.startswith("/api/i18n/")
                or parsed.path == "/api/room_2d_editor.js"):
                # Server CWD is HTML_DIR (web/); strip the /api prefix so the
                # SimpleHTTPRequestHandler resolves to web/theme/..., web/js/..., etc.
                self.path = self.path[len("/api"):]
                super().do_GET()
                return
            # Fall through to the normal handler below WITHOUT the auth gate
            # for other GET paths (server-info, etc.). A synthetic admin session
            # is injected via _loopback_session so downstream handlers still work.
            self._loopback_bypass = True

        # --- Auth gate ---
        # Static files (non-API GET) bypass auth so the app loads as overlay
        if not parsed.path.startswith("/api/"):
            super().do_GET()
            return
        if getattr(self, "_loopback_bypass", False):
            session = {"username": "__loopback__", "role": "admin", "_token": "__loopback__"}
        else:
            session = _require_auth(self)
        if session is None:
            return

        # --- Admin API (GET) ---
        if parsed.path.startswith("/api/admin/"):
            if session.get("role") != "admin":
                _send_json(self, 403, {"error": "Admin access required"})
                return
            if parsed.path == "/api/admin/users":
                handle_admin_users_get(self)
            elif parsed.path == "/api/admin/sessions":
                handle_admin_sessions_get(self)
            elif parsed.path == "/api/admin/bans":
                handle_admin_bans_get(self)
            else:
                self.send_error(404)
            return

        if parsed.path.startswith("/api/liquid/runs/"):
            from liquid_runtime_api import handle_get as handle_liquid_runtime_get
            handle_liquid_runtime_get(self, parsed.path, query)
            return

        if parsed.path == "/api/server-info":
            handle_server_info(self)
        elif parsed.path == "/api/log-stream":
            handle_log_stream(self, query)
        elif parsed.path == "/api/job-status":
            handle_job_status(self, query)
        elif parsed.path == "/api/check-mesh":
            handle_check_mesh(self)
        elif parsed.path == "/api/browser/state":
            handle_browser_state(self)
        elif parsed.path == "/api/browser/geometry-status":
            handle_browser_geometry_status(self)
        elif parsed.path == "/api/browser/screenshot":
            handle_browser_screenshot(self)
        elif parsed.path == "/api/browser/logs":
            handle_browser_logs(self)
        elif parsed.path == "/api/scan-case-status":
            handle_scan_case_status(self)
        elif parsed.path == "/api/browse-dir":
            handle_browse_dir(self, query)
        elif parsed.path == "/api/read-file":
            handle_read_file(self, query)
        elif parsed.path == "/api/read-file-binary":
            handle_read_file_binary(self, query)
        elif parsed.path == "/api/pp/mesh":
            handle_pp_mesh(self)
        elif parsed.path == "/api/pp/render-source":
            handle_pp_render_source(self)
        elif parsed.path == "/api/pp/mesh/binary":
            handle_pp_mesh_binary(self)
        elif parsed.path.startswith("/api/pp/field/"):
            field_name = parsed.path.split("/api/pp/field/")[1]
            handle_pp_field(self, field_name)
        elif parsed.path == "/api/pp/ai/demo-model-path":
            handle_pp_ai_demo_model_path(self)
        elif parsed.path == "/api/pp/ai/status":
            handle_pp_ai_status(self)
        elif parsed.path == "/api/pp/runs":
            handle_pp_runs(self)
        elif parsed.path == "/api/mesh-preview/binary":
            handle_mesh_preview_binary(self, query)
        elif parsed.path == "/api/mesh-preview/render-source":
            handle_mesh_preview_render_source(self, query)
        elif parsed.path == "/api/mesh-preview/debug":
            handle_mesh_preview_debug(self)
        elif parsed.path == "/api/server-patches":
            handle_server_patches(self)
        elif parsed.path == "/api/probes-latest":
            handle_probes_latest(self)
            return
        elif parsed.path == "/api/liquid/results":
            handle_liquid_results(self)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        # --- Public paths ---
        if parsed.path == "/api/auth/login":
            handle_auth_login(self)
            return

        # --- Auth gate ---
        session = _require_auth(self)
        if session is None:
            return

        # --- Auth logout (any authenticated user) ---
        if parsed.path == "/api/auth/logout":
            handle_auth_logout(self)
            return

        # --- Admin API (POST/DELETE-via-POST) ---
        if parsed.path.startswith("/api/admin/"):
            if session.get("role") != "admin":
                _send_json(self, 403, {"error": "Admin access required"})
                return
            # POST /api/admin/users -- create user
            if parsed.path == "/api/admin/users":
                handle_admin_users_post(self)
                return
            # Route /api/admin/users/<name>/password or /role
            m = re.match(r"^/api/admin/users/([A-Za-z0-9_]+)/password$", parsed.path)
            if m:
                handle_admin_user_password(self, m.group(1))
                return
            m = re.match(r"^/api/admin/users/([A-Za-z0-9_]+)/role$", parsed.path)
            if m:
                handle_admin_user_role(self, m.group(1))
                return
            # DELETE-via-POST: /api/admin/users/<name>
            m = re.match(r"^/api/admin/users/([A-Za-z0-9_]+)$", parsed.path)
            if m:
                handle_admin_user_delete(self, m.group(1))
                return
            # DELETE-via-POST: /api/admin/sessions/<prefix>
            m = re.match(r"^/api/admin/sessions/([a-f0-9]+)$", parsed.path)
            if m:
                handle_admin_session_delete(self, m.group(1))
                return
            # DELETE-via-POST: /api/admin/bans/<ip>
            m = re.match(r"^/api/admin/bans/(.+)$", parsed.path)
            if m:
                handle_admin_ban_delete(self, m.group(1))
                return
            # Upgrade / restart
            if parsed.path == "/api/admin/upgrade":
                handle_admin_upgrade(self)
                return
            if parsed.path == "/api/admin/upgrade/force":
                handle_admin_upgrade_force(self)
                return
            if parsed.path == "/api/admin/restart":
                handle_admin_restart(self)
                return
            self.send_error(404)
            return

        # --- Role check for non-viewer paths ---
        if parsed.path not in VIEWER_POST_PATHS and session.get("role") != "admin":
            _send_json(self, 403, {"error": "Admin access required"})
            return

        if parsed.path == "/api/liquid/runs" or parsed.path.startswith("/api/liquid/runs/"):
            from liquid_runtime_api import handle_post as handle_liquid_runtime_post
            with CASE_DIR_LOCK:
                liquid_case_dir = CASE_DIR
            handle_liquid_runtime_post(self, parsed.path, case_dir=liquid_case_dir)
            return

        if parsed.path == '/api/ov/result-dataset':
            handle_ov_result_dataset(self)
            return
        if parsed.path == '/api/ov/liquid-result-grant':
            handle_ov_liquid_result_grant(self)
            return
        if parsed.path == '/api/render-scene/export':
            handle_render_scene_export(self)
        elif parsed.path == "/api/set-case-dir":
            handle_set_case_dir(self)
        elif parsed.path == "/api/write-file":
            handle_write_file(self)
        elif parsed.path == "/api/create-dir":
            handle_create_dir(self)
        elif parsed.path == "/api/run-mesh":
            handle_run_mesh(self)
        elif parsed.path == "/api/prepare-case":
            handle_prepare_case(self)
        elif parsed.path == "/api/abort-job":
            handle_abort_job(self)
        elif parsed.path == "/api/screenshot":
            handle_screenshot(self)
        elif parsed.path == "/api/browser/close":
            handle_browser_close(self)
        elif parsed.path == "/api/browser/exec-js":
            handle_browser_exec_js(self)
        elif parsed.path == "/api/browser/load-proj":
            handle_browser_load_proj(self)
        elif parsed.path == "/api/browser/click":
            handle_browser_click(self)
        elif parsed.path in ("/api/browser/export-cfdrun", "/api/browser/export-cfd"):
            # 2026-04-18 rename: primary route is /api/browser/export-cfdrun.
            # /api/browser/export-cfd kept as a silent alias during the
            # deprecation window (no warn — the endpoint is semantically
            # identical, just renamed).
            handle_browser_export_cfdrun(self)
        elif parsed.path == "/api/browser/export-stl":
            handle_browser_export_stl(self)
        elif parsed.path == "/api/browser/export-case":
            handle_browser_export_case(self)
        elif parsed.path == "/api/browser/full-pipeline":
            handle_browser_full_pipeline(self)
        elif parsed.path == "/api/pp/init":
            handle_pp_init(self)
        elif parsed.path == "/api/mesh-preview/load":
            handle_mesh_preview_load(self)
        elif parsed.path == "/api/run-check-mesh":
            handle_run_check_mesh(self)
        elif parsed.path == "/api/pp/streamlines":
            handle_pp_streamlines(self)
        elif parsed.path == "/api/pp/slice":
            handle_pp_slice(self)
        elif parsed.path == "/api/pp/volume":
            handle_pp_volume(self)
        elif parsed.path == "/api/pp/ai/start":
            handle_pp_ai_start(self)
        elif parsed.path == "/api/pp/ai/infer":
            handle_pp_ai_infer(self)
        elif parsed.path == "/api/liquid/export-liquidrun":
            handle_liquid_export_liquidrun(self)
        elif parsed.path == "/api/liquid/solve":
            handle_liquid_solve(self)
        elif parsed.path == "/api/liquid/transient":
            handle_liquid_transient(self)
        elif parsed.path == "/api/run-coupled-sim":
            handle_run_coupled_sim(self)
        elif parsed.path == "/api/harvest-sensor-results":
            handle_harvest_sensor_results(self)
            return
        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)

        # --- Auth gate ---
        session = _require_admin(self)
        if session is None:
            return

        if AUTH_MGR is None:
            self.send_error(404)
            return

        # DELETE /api/admin/users/<name>
        m = re.match(r"^/api/admin/users/([A-Za-z0-9_]+)$", parsed.path)
        if m:
            handle_admin_user_delete(self, m.group(1))
            return
        # DELETE /api/admin/sessions/<prefix>
        m = re.match(r"^/api/admin/sessions/([a-f0-9]+)$", parsed.path)
        if m:
            handle_admin_session_delete(self, m.group(1))
            return
        # DELETE /api/admin/bans/<ip>
        m = re.match(r"^/api/admin/bans/(.+)$", parsed.path)
        if m:
            handle_admin_ban_delete(self, m.group(1))
            return

        self.send_error(404)


# ---------------------------------------------------------------------------
# WebSocket Terminal Server (PTY)
# ---------------------------------------------------------------------------

WS_PORT = 8006

def start_websocket_terminal_server(host: str, port: int) -> None:
    """Start a WebSocket server that provides an interactive terminal via PTY."""
    import websockets.sync.server as ws_server

    def terminal_handler(websocket):
        """Handle one terminal WebSocket connection."""
        # --- Auth check for WebSocket ---
        if AUTH_MGR is not None:
            # Loopback bypass: same-host connections (e.g. the browser running
            # on the server itself, or the in-process headless worker) inherit
            # the /api/auth/check loopback admin grant instead of requiring a
            # session token. Non-loopback peers still need a valid ?token=...
            ws_peer_ip = ""
            try:
                ws_peer_ip = websocket.remote_address[0] if websocket.remote_address else ""
            except Exception:
                ws_peer_ip = ""
            if ws_peer_ip in ("127.0.0.1", "::1"):
                ws_user = "__loopback__"
            else:
                ws_path = websocket.request.path if hasattr(websocket, 'request') else ""
                qs = parse_qs(urlparse(ws_path).query)
                ws_token = qs.get("token", [None])[0]
                session = AUTH_MGR.validate_session(ws_token)
                if not session:
                    websocket.close(4001, "Authentication required")
                    return
                if session.get("role") != "admin":
                    websocket.close(4001, "Admin access required")
                    return
                ws_user = session.get("username", "unknown")
                ws_ip = websocket.request.headers.get("X-Forwarded-For", "unknown")
                AUTH_MGR.log(f"TERMINAL_CONNECT: user={ws_user} ip={ws_ip}")
        else:
            ws_user = "anonymous"

        # Create PTY pair
        master_fd, slave_fd = pty.openpty()

        # Set initial terminal size
        s = struct.pack("HHHH", 30, 120, 0, 0)
        fcntl.ioctl(master_fd, 0x5414, s)  # TIOCSWINSZ

        # Start bash subprocess attached to the slave PTY
        # Auto-activate the same conda env that server.py is running in
        work_dir = str(CASE_DIR) if CASE_DIR else str(HTML_DIR)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        # Detect conda env from the Python executable path
        py_path = sys.executable
        conda_env = None
        if "/envs/" in py_path:
            conda_env = py_path.split("/envs/")[1].split("/")[0]

        # Write a temporary rcfile that activates conda env
        import tempfile
        rc_content = "source ~/.bashrc 2>/dev/null\n"
        if conda_env:
            rc_content += f"conda activate {conda_env}\n"
        rc_content += f"cd {work_dir}\n"
        rc_file = tempfile.NamedTemporaryFile(mode='w', suffix='.bashrc', delete=False)
        rc_file.write(rc_content)
        rc_file.close()

        proc = subprocess.Popen(
            ["bash", "--rcfile", rc_file.name, "-i"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=work_dir, env=env,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave_fd)  # Parent doesn't need slave

        # Set master to non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        try:
            while proc.poll() is None:
                # Read from WebSocket (user keystrokes)
                try:
                    msg = websocket.recv(timeout=0.05)
                    if isinstance(msg, str):
                        if msg.startswith('{"type":"resize"'):
                            try:
                                data = json.loads(msg)
                                rows = data.get("rows", 24)
                                cols = data.get("cols", 80)
                                s = struct.pack("HHHH", rows, cols, 0, 0)
                                fcntl.ioctl(master_fd, 0x5414, s)
                            except Exception:
                                pass
                        else:
                            os.write(master_fd, msg.encode("utf-8"))
                    elif isinstance(msg, bytes):
                        os.write(master_fd, msg)
                except TimeoutError:
                    pass
                except Exception:
                    break

                # Read from PTY (terminal output)
                try:
                    r, _, _ = select.select([master_fd], [], [], 0)
                    if r:
                        output = os.read(master_fd, 16384)
                        if output:
                            websocket.send(output.decode("utf-8", errors="replace"))
                except (OSError, IOError):
                    break
        except Exception:
            pass
        finally:
            try:
                os.close(master_fd)
            except Exception:
                pass
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
            if AUTH_MGR is not None:
                AUTH_MGR.log(f"TERMINAL_DISCONNECT: user={ws_user}")

    def _run():
        with ws_server.serve(terminal_handler, host or "127.0.0.1", port) as server:
            print(f"Terminal WebSocket: ws://localhost:{port}")
            server.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="STL Generator Backend Server")
    parser.add_argument("--app", choices=("react", "html"), default="react",
                        help="which editor to serve: react (default) or the "
                             "archived single-file html editor")
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--ws-port", type=int, default=8006)
    parser.add_argument("--host", type=str, default="")
    parser.add_argument("--case-dir", type=str, default=None,
                        help="Path to the OpenFOAM case directory (created if absent)")
    parser.add_argument("--password", type=str, default=None,
                        help="[DEPRECATED] Use the auth_manager user system instead. "
                             "This flag is ignored.")
    args = parser.parse_args()

    # --- Auth setup (auth_manager-backed) ---
    if args.password:
        print("WARNING: --password is deprecated and ignored. "
              "Use the built-in user management system (default admin/WIN#@desk).")
    auth_dir = Path(__file__).parent / "auth"
    log_path = Path(__file__).parent / "webserver.log"
    AUTH_MGR = AuthManager(auth_dir=auth_dir, log_path=log_path)
    print(f"Auth: ENABLED (user-based auth, data in {auth_dir})")

    if args.case_dir:
        p = Path(args.case_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        CASE_DIR = p

    APP_MODE = args.app
    if APP_MODE == "react" and not (REACT_DIST / "index.html").is_file():
        print(f"ERROR: --app react but no build at {REACT_DIST}", file=sys.stderr)
        print("       cd src/frontend/react && npm run build", file=sys.stderr)
        print("       (or --app html for the archived editor)", file=sys.stderr)
        sys.exit(2)
    if APP_MODE == "html" and not (LEGACY_DIR / "STL_generate.html").is_file():
        print(f"ERROR: --app html but no page at {LEGACY_DIR}", file=sys.stderr)
        print("       git checkout legacy-html-editor-last-working -- src/frontend/web",
              file=sys.stderr)
        sys.exit(2)

    # The CWD is the SHARED root either way: js/, vendor/, i18n/ and textures/
    # are what both editors load, and the legacy page's own files are redirected
    # to `web/` in `translate_path`.
    os.chdir(HTML_DIR)

    print(f"Python: {sys.executable}")
    print(f"dcdtuni: {'✓ available' if DCTWIN_AVAILABLE else '✗ NOT available — ' + DCTWIN_VERSION}")
    print(f"Case dir: {CASE_DIR or '(not set — configure via browser or --case-dir)'}")

    # Start WebSocket terminal server
    WS_PORT = args.ws_port
    start_websocket_terminal_server(args.host, WS_PORT)

    atexit.register(_shutdown_browser)

    def _sigterm_handler(signum, frame):
        _shutdown_browser()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm_handler)

    with http.server.ThreadingHTTPServer((args.host, args.port), STLServerHandler) as httpd:
        print(f"Server: http://localhost:{args.port}/")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            _shutdown_browser()
            print("\nServer stopped.")
