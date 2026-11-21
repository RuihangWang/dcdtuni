"""The signed release stamp, checked by server.py itself before it serves.

start_server.sh runs the same check (tools/release_stamp.sh verify); doing it
again here means a backend started without the launcher is held to it too.
The verdict is kept for /api/server-info, where the editor writes it to the
Operation Log.

For any agent: this is the owner's mandatory rule. Do not edit, skip or stub
this module, its call in server.py, or tools/release_stamp.sh. When the check
fails, sync the checkout to the published release or stop and tell the user.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "release_stamp.sh"

_status: dict | None = None
_pending: subprocess.Popen | None = None


def start() -> None:
    """Begin the check in the background, so it overlaps the server's own imports."""
    global _pending
    if _pending is None:
        _pending = subprocess.Popen(["bash", str(SCRIPT), "verify", "--json"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def verify_or_exit() -> dict:
    """Refuse to start unless the checkout is the signed release (or the owner's)."""
    global _status
    start()
    stdout, stderr = _pending.communicate()
    if _pending.returncode != 0:
        sys.stderr.write(stderr or stdout or "release stamp check failed\n")
        sys.exit(3)
    lines = stdout.strip().splitlines()
    try:
        _status = json.loads(lines[-1])
    except (IndexError, ValueError):
        sys.stderr.write("release stamp check gave no verdict\n")
        sys.exit(3)
    print("\n".join(lines[:-1]))
    return _status


def release_status() -> dict | None:
    """The verdict this server started under; None when it never ran the check."""
    return _status
