"""Administrator login for the repository's own scripts and probes.

The server has no built-in password any more, so a script cannot assume the
pair it was written with. These helpers pick the credentials with the same
precedence ``src/start_server.sh`` starts the server with:

1. the ``DCDTUNI_ADMIN_USER`` / ``DCDTUNI_ADMIN_PASSWORD`` environment pair;
2. the launcher's local-mode file ``src/server/auth-local/admin_credentials``
   (username on line 1, password on line 2), which a plain
   ``./src/start_server.sh`` creates on its first run;
3. the pair the calling script was written with, passed as ``fallback``.

The port comes from ``DCDTUNI_PORT`` and defaults to 8000, the launcher's
default. Standard library only, so the browser-use probes run under any
``python3``::

    sys.path.insert(0, str(REPO / "src" / "frontend" / "scripts"))
    from admin_login import base_url, login_js, login_payload

The shell scripts use the same precedence through ``admin_login.sh``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Tuple

# scripts/ -> frontend/ -> src/ -> repo
REPO = Path(__file__).resolve().parents[3]
CREDENTIALS_FILE = REPO / "src" / "server" / "auth-local" / "admin_credentials"
DEFAULT_PORT = 8000

Pair = Tuple[str, str]


def admin_credentials(fallback: Optional[Pair] = None) -> Pair:
    """Return ``(username, password)``: environment pair, local-mode file, fallback."""
    user = os.environ.get("DCDTUNI_ADMIN_USER")
    password = os.environ.get("DCDTUNI_ADMIN_PASSWORD")
    if user and password:
        return user, password
    try:
        lines = CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if len(lines) >= 2 and lines[0].strip() and lines[1]:
        return lines[0].strip(), lines[1]
    if fallback:
        return fallback
    raise SystemExit(
        "no administrator credentials: set DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD, "
        f"or start the server once with ./src/start_server.sh to create {CREDENTIALS_FILE}"
    )


def port() -> str:
    """The server port to drive: ``DCDTUNI_PORT``, default 8000."""
    return os.environ.get("DCDTUNI_PORT") or str(DEFAULT_PORT)


def base_url() -> str:
    return f"http://localhost:{port()}"


def login_payload(fallback: Optional[Pair] = None) -> dict:
    """The JSON body of ``POST /api/auth/login``."""
    user, password = admin_credentials(fallback)
    return {"username": user, "password": password}


def login_js(fallback: Optional[Pair] = None) -> str:
    """JavaScript statements that log the open page in.

    A synchronous same-origin POST, so the session cookie is set before the
    next statement runs. It throws when the server refuses the login, so a
    probe stops at the login rather than reading 401 bodies further on.
    """
    body = json.dumps(json.dumps(login_payload(fallback)))
    return (
        "var x=new XMLHttpRequest();x.open('POST','/api/auth/login',false);"
        "x.setRequestHeader('Content-Type','application/json');"
        f"x.send({body});"
        "if(x.status!==200)throw new Error('login refused: HTTP '+x.status+' '+x.responseText);"
    )
