"""Owned Linux process groups: identify, terminate and reap a private group the caller started.

The frozen ``training/structured_process_groups_v2.py`` (v1 with ESRCH after
exit treated as a disappearance). A child is admitted only if it leads its own
session and group with this user's uid; every signal goes to that group alone,
after re-checking the leader's start tick, so a reused PID or a foreign member
is refused instead of signalled. Start the child with
``subprocess.Popen(..., start_new_session=True)``.
"""
from __future__ import annotations

import ctypes
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PR_SET_CHILD_SUBREAPER = 36


def identity(pid: int) -> Optional[Dict[str, Any]]:
    base = Path("/proc") / str(pid)
    try:
        before = (base / "stat").read_text().rsplit(") ", 1)[1].split()
        uid = base.stat().st_uid
        after = (base / "stat").read_text().rsplit(") ", 1)[1].split()
        if before[19] != after[19]:
            raise RuntimeError("PID changed while observing")
        return {"pid": pid, "starttick": int(after[19]), "pgid": int(after[2]), "session": int(after[3]),
                "ppid": int(after[1]), "state": after[0], "uid": uid}
    except (FileNotFoundError, ProcessLookupError):
        return None


def group_members(owner: Dict[str, Any]) -> List[Dict[str, Any]]:
    leader = identity(owner["pid"])
    if leader is not None and (leader["starttick"] != owner["starttick"] or leader["uid"] != owner["uid"]
                               or leader["pgid"] != owner["pid"] or leader["session"] != owner["pid"]):
        raise RuntimeError("owned group leader identity changed; do not signal")
    found = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        row = identity(int(path.name))
        if row is None or row["pgid"] != owner["pid"]:
            continue
        if row["uid"] != owner["uid"] or row["session"] != owner["pid"] or row["starttick"] < owner["starttick"]:
            raise RuntimeError("foreign/reused process group member; do not signal")
        found.append(row)
    return found


def live_members(owner: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in group_members(owner) if r["state"] not in ("Z", "X")]


def enable_subreaper() -> bool:
    """Become the reaper of orphaned descendants (``PR_SET_CHILD_SUBREAPER``)."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot establish owned orphan reaping")
    return True


def owned_identity(child) -> Dict[str, Any]:
    """The identity of a ``Popen`` child that leads its own session and group, or ``RuntimeError``."""
    value = identity(child.pid)
    if value is None:
        child.wait()
        raise RuntimeError("child exited before identity admission; terminal error retained")
    if value["pgid"] != child.pid or value["session"] != child.pid or value["uid"] != os.getuid():
        raise RuntimeError("child does not have its own authenticated group")
    return value


def reap_owned_orphans(owner: Dict[str, Any], *, exclude_leader: bool = False) -> None:
    for row in group_members(owner):
        if row["ppid"] != os.getpid() or (exclude_leader and row["pid"] == owner["pid"]):
            continue
        try:
            os.waitpid(row["pid"], os.WNOHANG)
        except ChildProcessError:
            pass


def _signal_group(owner: Dict[str, Any], sig: int) -> None:
    if live_members(owner):
        try:
            os.killpg(owner["pid"], sig)
        except ProcessLookupError:
            pass


def terminate_owned(child, owner: Dict[str, Any], *, grace_seconds: float = 3) -> Dict[str, Any]:
    """TERM then KILL the complete private group, even if its leader exits first."""
    _signal_group(owner, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and live_members(owner):
        child.poll()
        reap_owned_orphans(owner, exclude_leader=True)
        time.sleep(.02)
    if live_members(owner):
        _signal_group(owner, signal.SIGKILL)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and live_members(owner):
        child.poll()
        reap_owned_orphans(owner, exclude_leader=True)
        time.sleep(.02)
    # Popen alone consumes the leader status; an orphan helper must not hide it.
    child.wait(timeout=grace_seconds)
    deadline = time.monotonic() + grace_seconds
    while group_members(owner) and time.monotonic() < deadline:
        reap_owned_orphans(owner, exclude_leader=True)
        time.sleep(.01)
    if group_members(owner):
        raise RuntimeError("owned descendants not fully reaped after escalation")
    return {"leader_reaped": True, "live_group_members": 0, "remaining_zombies": 0,
            "all_owned_group_members_reaped": True}


def descendant_group_owners(root_owner: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Snapshot private nested groups while their ancestry is still provable."""
    root = identity(root_owner["pid"])
    if root is not None and root["starttick"] != root_owner["starttick"]:
        raise RuntimeError("root PID reused")
    rows = {}
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            row = identity(int(path.name))
            if row is not None:
                rows[row["pid"]] = row

    def descendant(row):
        seen = set()
        while row["ppid"] in rows and row["pid"] not in seen:
            seen.add(row["pid"])
            if row["ppid"] == root_owner["pid"]:
                return True
            row = rows[row["ppid"]]
        return False
    owners = {}
    for row in rows.values():
        if row["uid"] != root_owner["uid"] or row["starttick"] < root_owner["starttick"] or not descendant(row):
            continue
        pgid = row["pgid"]
        if pgid == root_owner["pid"]:
            continue
        leader = rows.get(pgid)
        if leader is None or leader["pgid"] != pgid or leader["session"] != pgid or leader["uid"] != root_owner["uid"]:
            raise RuntimeError("unprovable nested process-group ownership")
        owners[pgid] = leader
    return owners


def terminate_group_owner(owner: Dict[str, Any], *, grace_seconds: float = 3) -> Dict[str, Any]:
    """Stop a previously ancestry-admitted nested group, even after reparenting."""
    _signal_group(owner, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and live_members(owner):
        reap_owned_orphans(owner)
        time.sleep(.02)
    if live_members(owner):
        _signal_group(owner, signal.SIGKILL)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and live_members(owner):
        reap_owned_orphans(owner)
        time.sleep(.02)
    deadline = time.monotonic() + grace_seconds
    while group_members(owner) and time.monotonic() < deadline:
        reap_owned_orphans(owner)
        time.sleep(.01)
    if group_members(owner):
        raise RuntimeError("owned nested group not fully reaped")
    return {"live_group_members": 0, "remaining_zombies": 0, "all_owned_group_members_reaped": True}
