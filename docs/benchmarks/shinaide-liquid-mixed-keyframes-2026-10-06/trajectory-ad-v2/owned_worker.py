"""Task-owned worker lifecycle using a child-held file sentinel."""
from __future__ import annotations
import json
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

class IdentityUnavailable(RuntimeError):
    pass

@dataclass(frozen=True)
class Identity:
    pid: int
    uid: int
    ppid: int
    startticks: str
    cwd: str
    executable: str
    argv: tuple[str, ...]

def proc_identity(pid: int) -> Identity:
    root = Path("/proc") / str(pid)
    data = (root / "stat").read_text()
    fields = data[data.rfind(")") + 2:].split()
    uids = next(line.split()[1:] for line in (root / "status").read_text().splitlines() if line.startswith("Uid:"))
    if len(set(uids)) != 1:
        raise IdentityUnavailable("worker real/effective/saved/fs UIDs differ")
    argv = tuple(x.decode() for x in (root / "cmdline").read_bytes().split(b"\0") if x)
    return Identity(pid, int(uids[0]), int(fields[1]), fields[19],
                    os.readlink(root / "cwd"), os.readlink(root / "exe"), argv)

def descendants(pid: int) -> list[int]:
    """Read every thread's child list; any subprocess child is unsupported."""
    result: set[int] = set()
    for task in (Path("/proc") / str(pid) / "task").iterdir():
        result.update(int(x) for x in (task / "children").read_text().split())
    return sorted(result)

def check_observation(expected: Identity, observation: dict) -> list[str]:
    reasons = []
    actual = observation.get("identity")
    if actual != expected:
        reasons.append("processIdentityMismatch")
    sent = observation.get("sentinel", {})
    if not sent.get("regular") or sent.get("symlink"):
        reasons.append("sentinelNotRegular")
    if sent.get("mode") != 0o600 or sent.get("uid") != expected.uid:
        reasons.append("sentinelPermissionsMismatch")
    if sent.get("deviceInode") != observation.get("expectedDeviceInode"):
        reasons.append("sentinelInodeMismatch")
    if observation.get("heldFdDeviceInode") != observation.get("expectedDeviceInode"):
        reasons.append("heldFdMismatch")
    if observation.get("heldFdPath") != observation.get("expectedPath"):
        reasons.append("heldFdPathMismatch")
    if observation.get("owners") != [expected.pid]:
        reasons.append("sentinelOwnersMismatch")
    if observation.get("children") != []:
        reasons.append("unexpectedDescendants")
    if observation.get("queryError"):
        reasons.append("identityQueryIncomplete")
    return reasons

def observe(expected: Identity, sentinel: Path, held_fd: int, device_inode: tuple[int, int]) -> dict:
    result = {"expectedDeviceInode": device_inode, "expectedPath": str(sentinel)}
    try:
        result["identity"] = proc_identity(expected.pid)
        st = sentinel.lstat()
        result["sentinel"] = {"regular": stat.S_ISREG(st.st_mode), "symlink": stat.S_ISLNK(st.st_mode),
                              "mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid,
                              "deviceInode": (st.st_dev, st.st_ino)}
        fdpath = Path("/proc") / str(expected.pid) / "fd" / str(held_fd)
        fdstat = fdpath.stat()
        result["heldFdDeviceInode"] = (fdstat.st_dev, fdstat.st_ino)
        result["heldFdPath"] = os.readlink(fdpath)
        result["children"] = descendants(expected.pid)
        quiet = subprocess.run(["fuser", "-s", str(sentinel)], capture_output=True)
        listing = subprocess.run(["fuser", str(sentinel)], capture_output=True)
        if quiet.returncode != 0 or listing.returncode != 0:
            result["queryError"] = "sentinelNotHeld"
        words = listing.stdout.decode().split()
        if any(not x.isdigit() for x in words):
            result["queryError"] = "fuserUnexpectedOutput"
            result["owners"] = []
        else:
            result["owners"] = sorted(set(int(x) for x in words))
    except (OSError, ProcessLookupError, IdentityUnavailable) as error:
        result["queryError"] = type(error).__name__
    return result

def ready_record(fd: int) -> dict:
    identity = proc_identity(os.getpid())
    st = os.fstat(fd)
    return {"identity": identity.__dict__, "heldFd": fd, "deviceInode": [st.st_dev, st.st_ino]}

def hold_worker_sentinel(output: Path, expected_parent: int):
    """Only called by --worker; parent never opens this file."""
    if os.getppid() != expected_parent:
        raise ValueError("worker parent differs")
    fd = os.open(output / "worker.sentinel", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        record = ready_record(fd)
        temporary = output / "worker-ready.partial"
        temporary.write_text(json.dumps(record, indent=2) + "\n")
        temporary.replace(output / "worker-ready.json")
    except BaseException:
        os.close(fd)
        raise
    return fd

def request_stop(expected: Identity, sentinel: Path, held_fd: int, device_inode: tuple[int, int],
                 signal_name: str, observer=observe, runner=subprocess.run) -> dict:
    if signal_name not in ("TERM", "KILL"):
        raise ValueError("unsupported fuser signal")
    observation = observer(expected, sentinel, held_fd, device_inode)
    reasons = check_observation(expected, observation)
    if reasons:
        return {"signal": signal_name, "sent": False, "reasons": reasons}
    result = runner(["fuser", "-k", "-" + signal_name, str(sentinel)], capture_output=True)
    return {"signal": signal_name, "sent": result.returncode == 0,
            "returncode": result.returncode, "reasons": [] if result.returncode == 0 else ["fuserSignalFailed"]}

def run_owned(command: list[str], cwd: Path, env: dict, output: Path,
              deadline_seconds: float = 180) -> dict:
    started = time.monotonic()
    events: list[dict] = []
    with (output / "worker.stdout.log").open("w") as stdout, (output / "worker.stderr.log").open("w") as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            startticks = proc_identity(process.pid).startticks
        except (OSError, ProcessLookupError, IdentityUnavailable) as error:
            return {"passed": False, "incomplete": True, "reason": "workerIdentityUnavailable",
                    "identityError": type(error).__name__, "pid": process.pid,
                    "returncode": process.poll(), "events": events}
        expected = Identity(process.pid, os.getuid(), os.getpid(), startticks,
                            str(cwd.resolve()), str(Path(command[0]).resolve()), tuple(command))
        ready = None
        while time.monotonic() - started < min(10, deadline_seconds) and process.poll() is None:
            ready_path = output / "worker-ready.json"
            if ready_path.is_file():
                ready = json.loads(ready_path.read_text())
                break
            time.sleep(0.02)
        if ready is None:
            # Without a proven held sentinel no termination action is safe.
            return {"passed": False, "incomplete": True, "reason": "workerReadyMissing",
                    "pid": process.pid, "returncode": process.poll(), "events": events}
        ready_identity = dict(ready.get("identity", {}))
        ready_identity["argv"] = tuple(ready_identity.get("argv", ()))
        if ready_identity != expected.__dict__:
            return {"passed": False, "incomplete": True, "reason": "workerReadyIdentityMismatch",
                    "pid": process.pid, "returncode": process.poll(), "events": events}
        held_fd = ready["heldFd"]
        device_inode = tuple(ready["deviceInode"])
        try:
            process.wait(timeout=max(0, deadline_seconds - (time.monotonic() - started)))
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            for signal_name in ("TERM", "KILL"):
                if process.poll() is not None:
                    break
                event = request_stop(expected, output / "worker.sentinel", held_fd, device_inode, signal_name)
                events.append(event)
                if not event["sent"]:
                    break
                try:
                    process.wait(timeout=3)
                    break
                except subprocess.TimeoutExpired:
                    continue
        returncode = process.poll()
        return {"passed": not timed_out and returncode == 0, "incomplete": returncode is None,
                "timed_out": timed_out, "returncode": returncode, "pid": process.pid,
                "deadline_seconds": deadline_seconds, "conditional_wait_tail_max_seconds": 6,
                "fuser_query_and_signal_elapsed_included": True, "elapsed_seconds": time.monotonic() - started,
                "events": events, "identity": expected.__dict__,
                "sentinel": {"path": str(output / "worker.sentinel"), "deviceInode": device_inode, "heldFd": held_fd}}
