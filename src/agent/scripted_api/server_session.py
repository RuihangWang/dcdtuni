"""A private dcdtuni server for one scripted run, driven through its REST API.

The repository forbids end-to-end STL/mesh/solve paths that bypass the server
(CLAUDE.md debugging rules 1 and 8), so the scripted air run is a client of the
server, exactly like the editor:

* the server is started ONLY through ``src/start_server.sh`` (``--app api``:
  the Python backend serves ``/api`` only, so it needs neither the React build
  nor the archived ``STL_generate.html``), on a port pair this process verified
  free (and reserved against its other sessions), with its own ``--case-dir``;
* administrator credentials are generated per run and passed through the
  launcher's documented environment pair; ``--auth-dir`` is a fresh directory,
  so no shared user store is read or modified;
* jobs are submitted with the same ``/api/run-mesh`` body the editor sends and
  followed with ``/api/job-status`` and ``/api/log-stream``;
* the server is stopped with ``fuser -k -TERM <port>/tcp`` on its own two
  ports (the server exits cleanly on SIGTERM and the launcher's trap runs),
  then, only for a port still held after a grace period, ``fuser -k -KILL`` --
  each time after checking that every process on the port belongs to this user.

A server holds one global case directory (CLAUDE.md "Single-case server"), so
one scripted run owns one server; nothing here shares a server between runs.
"""
from __future__ import annotations

import getpass
import json
import os
import random
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.scripted_api.identity import REPO

LAUNCHER = REPO / "src" / "start_server.sh"


class ServerError(RuntimeError):
    def __init__(self, status: int, body: Any, path: str):
        self.status, self.body, self.path = status, body, path
        detail = body.get("error") if isinstance(body, dict) else body
        super().__init__(f"{path} -> HTTP {status}: {detail}")


def _port_free(port: int) -> bool:
    for host in ("127.0.0.1", "0.0.0.0"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return False
    return True


# Pairs handed to sessions of this process that have not been released yet: a
# batch starts several servers at once, and "free" is only true until one binds.
_RESERVED: set = set()
_RESERVED_LOCK = threading.Lock()


def free_port_pair(low: int = 18000, high: int = 28000, attempts: int = 400) -> int:
    """A random even port p with p and p+1 both unbound (the launcher's WS port is p+1).

    The pair is reserved for this process until :func:`release_port_pair`, so two
    sessions started concurrently never receive the same pair.
    """
    rng = random.Random(os.getpid() ^ time.time_ns() ^ threading.get_ident())
    for _ in range(attempts):
        port = rng.randrange(low, high, 2)
        with _RESERVED_LOCK:
            if port in _RESERVED:
                continue
            if _port_free(port) and _port_free(port + 1):
                _RESERVED.add(port)
                return port
    raise RuntimeError("no free port pair found")


def release_port_pair(port: Optional[int]) -> None:
    with _RESERVED_LOCK:
        _RESERVED.discard(port)


def _port_pids(port: int) -> List[int]:
    try:
        done = subprocess.run(["fuser", f"{port}/tcp"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    text = (done.stdout + " " + done.stderr).replace(f"{port}/tcp:", " ")
    return [int(token) for token in text.split() if token.isdigit()]


def _pid_user(pid: int) -> Optional[str]:
    try:
        done = subprocess.run(["ps", "-o", "user=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


class ServerSession:
    """Start, use and stop one private server. Use as a context manager."""

    def __init__(self, case_dir: Path, work_dir: Path, *, app: str = "api", startup_timeout: float = 120.0,
                 stop_grace: float = 15.0, gpu: Optional[str] = None):
        self.case_dir = Path(case_dir).resolve()
        # the launcher's --gpu (GPU air solver): None: not passed; "": all cards; else the list of cards
        self.gpu = gpu
        self.work_dir = Path(work_dir)
        self.app = app
        self.startup_timeout = startup_timeout
        self.stop_grace = stop_grace
        self.port: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self.cookie = ""
        self.log_path = self.work_dir / "server.log"
        self.auth_dir = self.work_dir / "server-auth"
        self.startup_seconds: Optional[float] = None
        self.stop_report: Dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "ServerSession":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        started = time.perf_counter()
        self.port = free_port_pair()
        self.auth_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        user, password = "api_" + secrets.token_hex(6), secrets.token_urlsafe(32)
        self._credentials = (user, password)
        env = {**os.environ, "DCDTUNI_ADMIN_USER": user, "DCDTUNI_ADMIN_PASSWORD": password}
        command = ["bash", str(LAUNCHER), "--app", self.app, "--port", str(self.port),
                   "--ws-port", str(self.port + 1), "--case-dir", str(self.case_dir),
                   "--auth-dir", str(self.auth_dir)]
        if self.gpu is not None:
            command += ["--gpu", self.gpu] if self.gpu else ["--gpu"]
        self.command = command
        log = open(self.log_path, "x", encoding="utf-8")
        try:
            self.process = subprocess.Popen(command, cwd=str(REPO), env=env, stdout=log,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                            start_new_session=True)
        finally:
            log.close()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"start_server.sh exited {self.process.returncode} before listening; "
                                   f"log tail: {self.log_tail()}")
            try:
                status, _ = self.request("GET", "/api/auth/check", timeout=5)
                if status in (200, 401, 403):
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        else:
            self.stop()
            raise RuntimeError(f"server did not answer on port {self.port} within {self.startup_timeout:.0f}s; "
                               f"log tail: {self.log_tail()}")
        self.login()
        self.startup_seconds = time.perf_counter() - started

    def stop(self, grace: Optional[float] = None) -> None:
        """SIGTERM the server on its own ports, SIGKILL only what outlives ``grace`` seconds.

        As the launcher's own cleanup does: the server's SIGTERM handler exits
        cleanly, the launcher's ``wait`` returns and its trap runs. A process on
        one of the ports that belongs to another user is never signalled.
        """
        if self.port is None:
            return
        grace = self.stop_grace if grace is None else grace
        me = getpass.getuser()
        report: Dict[str, Any] = {"ports": [self.port, self.port + 1], "freed": [], "refused": []}

        def mine(port: int) -> Tuple[List[int], Dict[int, Optional[str]], bool]:
            pids = _port_pids(port)
            owners = {pid: _pid_user(pid) for pid in pids}
            return pids, owners, bool(pids) and all(owner == me for owner in owners.values())

        termed: Dict[int, List[int]] = {}
        for port in (self.port, self.port + 1):
            pids, owners, owned = mine(port)
            if owned:
                subprocess.run(["fuser", "-k", "-TERM", f"{port}/tcp"], capture_output=True, timeout=10)
                termed[port] = pids
            elif pids:
                # Never signal a process that is not ours, even on "our" port.
                report["refused"].append({"port": port, "owners": owners})
        deadline = time.monotonic() + grace
        waiting = dict(termed)
        while waiting:
            waiting = {port: pids for port, pids in waiting.items() if _port_pids(port)}
            if not waiting or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        for port, pids in termed.items():
            signal = "TERM"
            if port in waiting:
                _, owners, owned = mine(port)
                if owned:
                    subprocess.run(["fuser", "-k", "-KILL", f"{port}/tcp"], capture_output=True, timeout=10)
                    signal = "KILL"
                elif owners:
                    report["refused"].append({"port": port, "owners": owners})
            report["freed"].append({"port": port, "pids": pids, "signal": signal})
        if self.process is not None:
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                report["launcherStillRunning"] = True
            report["launcherReturncode"] = self.process.returncode
        self.stop_report = report
        release_port_pair(self.port)
        self.port = None

    def log_tail(self, limit: int = 3000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    # -- HTTP --------------------------------------------------------------
    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, method: str, path: str, payload: Optional[dict] = None, *,
                timeout: float = 60.0) -> Tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, raw, headers = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, raw, headers = error.code, error.read(), error.headers
        cookie = headers.get("Set-Cookie") if headers else None
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text) if text.strip()[:1] in ("{", "[") else text
        except json.JSONDecodeError:
            body = text
        return status, body

    def call(self, method: str, path: str, payload: Optional[dict] = None, *, timeout: float = 60.0,
             expect: Tuple[int, ...] = (200,)) -> Any:
        """One JSON request; any status outside ``expect`` raises :class:`ServerError` (``POST /api/liquid/runs`` answers 201)."""
        status, body = self.request(method, path, payload, timeout=timeout)
        if status not in expect:
            raise ServerError(status, body, path)
        return body

    def login(self) -> None:
        user, password = self._credentials
        self.call("POST", "/api/auth/login", {"username": user, "password": password})
        if not self.cookie:
            raise RuntimeError("login succeeded but the server set no session cookie")

    # -- jobs --------------------------------------------------------------
    def submit_run(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("POST", "/api/run-mesh", {**body, "caseDir": str(self.case_dir)}, timeout=900)

    def job_status(self, job_id: str) -> Dict[str, Any]:
        return self.call("GET", f"/api/job-status?job_id={job_id}", timeout=30)

    def abort(self, job_id: str) -> Any:
        try:
            return self.call("POST", "/api/abort-job", {"jobId": job_id}, timeout=60)
        except Exception as exc:  # abort is best effort; the caller records it
            return {"error": str(exc)}

    def wait_job(self, job_id: str, *, timeout: float, poll: float = 3.0) -> Dict[str, Any]:
        """Poll until done. On timeout abort the job and return its final status."""
        started = time.monotonic()
        while True:
            status = self.job_status(job_id)
            if status.get("done"):
                status["timedOut"] = False
                return status
            if time.monotonic() - started > timeout:
                abort = self.abort(job_id)
                for _ in range(60):
                    time.sleep(1)
                    status = self.job_status(job_id)
                    if status.get("done"):
                        break
                status.update(timedOut=True, abortReply=abort)
                return status
            time.sleep(poll)

    def job_log(self, job_id: str, timeout: float = 120.0) -> Tuple[List[str], Optional[dict]]:
        """All captured lines of a finished job, read from /api/log-stream (SSE)."""
        request = urllib.request.Request(self.base + f"/api/log-stream?job_id={job_id}")
        request.add_header("Cookie", self.cookie)
        lines: List[str] = []
        final: Optional[dict] = None
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                text = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not text.startswith("data: "):
                    continue
                value = json.loads(text[6:])
                if isinstance(value, dict) and value.get("done") is True:
                    final = value
                    break
                lines.append(value if isinstance(value, str) else json.dumps(value))
        return lines, final
