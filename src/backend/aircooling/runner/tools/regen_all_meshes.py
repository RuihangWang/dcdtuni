#!/usr/bin/env python
"""Regenerate every case mesh and check it against two acceptance gates.

Gate 1 — time budget. A case that has not finished meshing within
``--timeout`` seconds (default 300 = 5 minutes) is aborted and recorded as a
FAILURE. Not "slow": failed. A mesh nobody will wait for is not a mesh.

Gate 2 — patch/solid correspondence. Every STL region the runner declared as a
boundary patch must exist in ``constant/polyMesh/boundary``, with faceZone
baffles counted as their cyclic master/slave pair. A declared region that
produced no patch is a lost boundary condition: the geometry is still there,
but nothing can be prescribed on it and the solve runs a quietly different
problem. See ``backend.aircooling.runner.mesh_acceptance``.

Meshing goes through the server REST API, not by importing the runner, so this
exercises the same path a user does.

    python -m backend.aircooling.runner.tools.regen_all_meshes   # every case/ with STLs
    python -m backend.aircooling.runner.tools.regen_all_meshes --cases Base_leakage,Slab
    python -m backend.aircooling.runner.tools.regen_all_meshes --timeout 300 --out /tmp/mesh_report.json

The server is single-case and serialises jobs (see CLAUDE.md), so this starts
its OWN server on its own port rather than sharing one.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import urllib.error
import urllib.request

# tools/ -> runner/ -> aircooling/ -> backend/ -> src/ -> repo
REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "src"))

from backend.aircooling.runner.mesh_acceptance import (  # noqa: E402
    check_patch_solid_correspondence,
)

def admin_credentials() -> tuple[str, str]:
    """Same precedence as src/start_server.sh: the DCDTUNI_ADMIN_USER /
    DCDTUNI_ADMIN_PASSWORD pair, else the launcher's local-mode file
    src/server/auth-local/admin_credentials (username, password on two lines)."""
    user = os.environ.get("DCDTUNI_ADMIN_USER")
    password = os.environ.get("DCDTUNI_ADMIN_PASSWORD")
    if user and password:
        return user, password
    path = REPO / "src" / "server" / "auth-local" / "admin_credentials"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(
            f"no administrator credentials: set DCDTUNI_ADMIN_USER/DCDTUNI_ADMIN_PASSWORD "
            f"or start the server once with ./src/start_server.sh to create {path}"
        ) from exc
    return lines[0].strip(), lines[1]


# --------------------------------------------------------------------------
# tiny HTTP helper (stdlib only, keeps the script dependency-free)
# --------------------------------------------------------------------------
class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookie = ""

    def _request(self, path: str, payload: Optional[dict] = None, timeout: float = 60.0):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        if data:
            req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            set_cookie = resp.headers.get("Set-Cookie")
            if set_cookie:
                self.cookie = set_cookie.split(";", 1)[0]
            body = resp.read().decode()
        return json.loads(body) if body.strip().startswith(("{", "[")) else body

    def login(self):
        return self._request(
            "/api/auth/login", dict(zip(("username", "password"), admin_credentials()))
        )

    def set_case_dir(self, case_dir: Path):
        return self._request("/api/set-case-dir", {"caseDir": str(case_dir)})

    def run_mesh(self):
        return self._request("/api/run-mesh", {"meshOnly": True})

    def job_status(self, job_id: str):
        return self._request(f"/api/job-status?job_id={job_id}")

    def log_stream(self, job_id: str, timeout: float = 30.0) -> str:
        """Fetch a finished job's captured log.

        Worth doing for every case: each mpirun step overwrites
        Mesh/log.mpi_solver, so by the time a run ends the on-disk log belongs
        to whichever step ran last — useless for diagnosing the step that
        actually failed. The server keeps the whole thing.
        """
        try:
            raw = self._request(f"/api/log-stream?job_id={job_id}", timeout=timeout)
        except Exception as exc:
            return f"[log fetch failed: {exc}]"
        if isinstance(raw, str):
            return raw
        return json.dumps(raw)

    def abort(self, job_id: str):
        try:
            return self._request("/api/abort-job", {"jobId": job_id})
        except Exception as exc:  # abort is best-effort
            return {"error": str(exc)}


def _free_port_pair(start: int = 8100) -> int:
    """Return p such that p and p+1 are both free."""
    for port in range(start, start + 200, 2):
        try:
            with socket.socket() as a, socket.socket() as b:
                a.bind(("127.0.0.1", port))
                b.bind(("127.0.0.1", port + 1))
            return port
        except OSError:
            continue
    raise RuntimeError("no free port pair found")


# --------------------------------------------------------------------------
@dataclass
class CaseResult:
    case: str
    seconds: float = 0.0
    timed_out: bool = False
    mesh_ok: bool = False
    returncode: Optional[int] = None
    n_cells: Optional[int] = None
    acceptance_ok: bool = False
    n_solids: int = 0
    n_patches: int = 0
    missing_patches: List[str] = field(default_factory=list)
    unexpected_patches: List[str] = field(default_factory=list)
    error: str = ""
    log_path: str = ""

    @property
    def passed(self) -> bool:
        return self.mesh_ok and not self.timed_out and self.acceptance_ok

    def line(self, budget: float) -> str:
        status = "PASS" if self.passed else "FAIL"
        bits = [f"{self.case:22s} {status}  {self.seconds:6.0f}s"]
        if self.timed_out:
            bits.append(f"TIMEOUT (>{budget:.0f}s)")
        elif not self.mesh_ok:
            bits.append(f"mesh failed rc={self.returncode} {self.error}".strip())
        else:
            bits.append(f"{self.n_cells or '?'} cells")
            bits.append(f"{self.n_solids} solids -> {self.n_patches} patches")
            if self.missing_patches:
                bits.append(
                    f"MISSING {len(self.missing_patches)}: "
                    + ",".join(self.missing_patches[:4])
                )
            if self.unexpected_patches:
                bits.append(
                    f"UNEXPECTED {len(self.unexpected_patches)}: "
                    + ",".join(self.unexpected_patches[:4])
                )
        return " | ".join(bits)


def _cell_count(mesh_dir: Path) -> Optional[int]:
    owner = mesh_dir / "constant" / "polyMesh" / "owner"
    if not owner.is_file():
        return None
    try:
        from backend.aircooling.runner.polymesh_io import read_owner_note

        return read_owner_note(owner).get("nCells")
    except Exception:
        return None


def run_case(
    sess: Session,
    case_dir: Path,
    budget: float,
    poll: float = 5.0,
    log_dir: Optional[Path] = None,
) -> CaseResult:
    result = CaseResult(case=case_dir.name)
    mesh_dir = case_dir / "Mesh"

    try:
        sess.set_case_dir(case_dir)
        job = sess.run_mesh()
        job_id = job.get("jobId")
        if not job_id:
            result.error = f"run-mesh returned {job}"
            return result
    except Exception as exc:
        result.error = f"submit failed: {exc}"
        return result

    started = time.time()
    while True:
        elapsed = time.time() - started
        if elapsed > budget:
            sess.abort(job_id)
            result.seconds = elapsed
            result.timed_out = True
            # give the server a moment to reap the aborted job
            time.sleep(5)
            if log_dir is not None:
                log_dir.mkdir(parents=True, exist_ok=True)
                path = log_dir / f"{case_dir.name}.log"
                path.write_text(
                    sess.log_stream(job_id), encoding="utf-8", errors="replace"
                )
                result.log_path = str(path)
            return result
        try:
            status = sess.job_status(job_id)
        except Exception as exc:
            result.error = f"status poll failed: {exc}"
            result.seconds = elapsed
            return result
        if status.get("done"):
            result.seconds = time.time() - started
            result.returncode = status.get("returncode")
            result.mesh_ok = status.get("returncode") == 0
            break
        time.sleep(poll)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{case_dir.name}.log"
        path.write_text(sess.log_stream(job_id), encoding="utf-8", errors="replace")
        result.log_path = str(path)

    result.n_cells = _cell_count(mesh_dir)

    acc = check_patch_solid_correspondence(mesh_dir, case_dir.name)
    result.acceptance_ok = acc.ok
    result.n_solids = acc.n_solids
    result.n_patches = acc.n_patches
    result.missing_patches = acc.missing_patches
    result.unexpected_patches = acc.unexpected_patches
    if acc.error and not result.error:
        result.error = acc.error
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default="", help="comma-separated case names (default: all)")
    ap.add_argument("--timeout", type=float, default=300.0, help="per-case budget in seconds")
    ap.add_argument("--case-root", default=str(REPO / "case"))
    ap.add_argument("--out", default="", help="write a JSON report here")
    ap.add_argument(
        "--log-dir",
        default="",
        help="save each case's full job log here (default: alongside --out)",
    )
    ap.add_argument("--port", type=int, default=0, help="use an already-running server on this port")
    args = ap.parse_args()

    case_root = Path(args.case_root)
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]

    cases = []
    for d in sorted(case_root.iterdir()):
        if not d.is_dir():
            continue
        if wanted and d.name not in wanted:
            continue
        if not (d / "Mesh" / "constant" / "triSurface").is_dir():
            continue
        if not list((d / "model").glob("*.cfdrun")) if (d / "model").is_dir() else True:
            continue
        cases.append(d)

    if not cases:
        print("no meshable cases found", file=sys.stderr)
        return 2

    server: Optional[subprocess.Popen] = None
    port = args.port
    if not port:
        port = _free_port_pair()
        log = Path(os.environ.get("TMPDIR", "/tmp")) / f"regen_server_{port}.log"
        print(f"starting server on {port}/{port + 1}  (log: {log})")
        server = subprocess.Popen(
            [
                "conda", "run", "--no-capture-output", "-n", "dcdtuni",
                sys.executable if "dcdtuni" in sys.executable else "python",
                str(REPO / "src" / "server" / "server.py"),
                "--port", str(port), "--ws-port", str(port + 1),
            ],
            stdout=log.open("w"), stderr=subprocess.STDOUT, cwd=str(REPO),
        )
        for _ in range(60):
            time.sleep(1)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                continue
        else:
            print("server did not come up", file=sys.stderr)
            server.terminate()
            return 3

    sess = Session(f"http://127.0.0.1:{port}")
    try:
        sess.login()

        log_dir = None
        if args.log_dir:
            log_dir = Path(args.log_dir)
        elif args.out:
            log_dir = Path(args.out).with_suffix("") / "logs"
        if log_dir:
            print(f"per-case logs -> {log_dir}")

        print(f"\n{len(cases)} cases, budget {args.timeout:.0f}s each\n" + "=" * 100)
        results: List[CaseResult] = []
        for i, case_dir in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {case_dir.name} ...", flush=True)
            r = run_case(sess, case_dir, args.timeout, log_dir=log_dir)
            results.append(r)
            print("        " + r.line(args.timeout), flush=True)

        print("=" * 100)
        n_pass = sum(r.passed for r in results)
        print(f"{n_pass}/{len(results)} PASS")
        slow = [r for r in results if r.timed_out]
        if slow:
            print(f"  timed out (> {args.timeout:.0f}s): " + ", ".join(r.case for r in slow))
        bad_patches = [r for r in results if r.mesh_ok and not r.acceptance_ok]
        if bad_patches:
            print("  patch/solid mismatch: " + ", ".join(r.case for r in bad_patches))

        if args.out:
            Path(args.out).write_text(
                json.dumps(
                    {
                        "budgetSeconds": args.timeout,
                        "results": [asdict(r) for r in results],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"  report -> {args.out}")

        return 0 if n_pass == len(results) else 1
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
