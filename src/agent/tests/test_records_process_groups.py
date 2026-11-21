"""Owned process groups: a child in its own session is identified, terminated and fully reaped."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agent.records import process_groups as groups

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc process groups")


def spawn(code="import time\ntime.sleep(60)"):
    return subprocess.Popen([sys.executable, "-c", code], start_new_session=True)


def test_identity_of_this_process():
    row = groups.identity(os.getpid())
    assert row["pid"] == os.getpid() and row["uid"] == os.getuid()
    assert groups.identity(2 ** 22 + 12345) is None


def test_owned_child_group_is_terminated_and_reaped():
    child = spawn("import subprocess,sys,time\n"
                  "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                  "time.sleep(60)")
    try:
        owner = groups.owned_identity(child)
        assert owner["pgid"] == child.pid == owner["session"]
        for _ in range(200):
            if len(groups.live_members(owner)) >= 2:
                break
            __import__("time").sleep(0.02)
        assert len(groups.live_members(owner)) >= 2
        report = groups.terminate_owned(child, owner, grace_seconds=3)
        assert report["all_owned_group_members_reaped"] is True
        assert groups.group_members(owner) == []
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_child_without_its_own_group_is_not_admitted():
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        with pytest.raises(RuntimeError, match="own authenticated group"):
            groups.owned_identity(child)
    finally:
        child.kill()
        child.wait()
