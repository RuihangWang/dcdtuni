"""Repository-local MPI startup and affinity for budgeted mesh steps."""
from __future__ import annotations

import os
import shlex
import time
from pathlib import Path
from typing import Mapping


def _core_key(cpu: int) -> tuple[str, str] | tuple[str, int]:
    topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
    try:
        return ((topology / "physical_package_id").read_text().strip(),
                (topology / "core_id").read_text().strip())
    except OSError:
        return ("cpu", cpu)


_BUSY = 0.5            # a CPU busier than this over the sample is taken
_FRESH_CLAIM_S = 30.0  # a claim this young holds its CPUs even while they look idle


def _cpu_busy(cpus) -> dict[int, float]:
    """Busy fraction of each CPU over a short /proc/stat sample ({} if unreadable)."""
    def read():
        out = {}
        try:
            for line in Path("/proc/stat").read_text().splitlines():
                name, *fields = line.split()
                if name.startswith("cpu") and name[3:].isdigit():
                    v = [int(x) for x in fields]
                    out[int(name[3:])] = (sum(v), v[3] + (v[4] if len(v) > 4 else 0))
        except (OSError, ValueError):
            pass
        return out
    a = read()
    time.sleep(0.25)
    b = read()
    busy = {}
    for cpu in cpus:
        if cpu in a and cpu in b:
            total, idle = b[cpu][0] - a[cpu][0], b[cpu][1] - a[cpu][1]
            if total > 0:
                busy[cpu] = 1.0 - idle / total
    return busy


def _claims_dir() -> Path | None:
    return Path(__file__).resolve().parents[4] / ".codex-tmp" / "mpi_cpu_claims"


def _pid_alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def _claimed_by_others(claims: Path | None, busy: Mapping[int, float]) -> set[int]:
    """CPUs another live server bound a mesh step to, while that claim still holds.

    A claim holds while it is fresh (servers starting together see the same idle
    CPUs) or while its CPUs are busy; a dead server's claim is removed.
    """
    taken: set[int] = set()
    if claims is None or not claims.is_dir():
        return taken
    now = time.time()
    for entry in claims.iterdir():
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if not _pid_alive(pid):
            entry.unlink(missing_ok=True)
            continue
        try:
            cpus = {int(x) for x in entry.read_text().split()}
            fresh = now - entry.stat().st_mtime < _FRESH_CLAIM_S
        except (OSError, ValueError):
            continue
        taken |= cpus if fresh else {c for c in cpus if busy.get(c, 0.0) > _BUSY}
    return taken


def _write_claim(claims: Path | None, cpus) -> None:
    if claims is None:
        return
    try:
        claims.mkdir(parents=True, exist_ok=True)
        (claims / str(os.getpid())).write_text(" ".join(map(str, cpus)) + "\n")
    except OSError:
        pass


def mesh_mpi_environment(command: str, *, mesh_budget_active: bool,
                         base_environment: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Pin one Hydra rank per idle allowed CPU, with physical cores first.

    Busy CPUs (another user's job) and CPUs another dcdtuni server on this host
    has claimed for its mesh are skipped: binding to the first N allowed CPUs
    stacked every concurrent mesh on the same cores (Base604, 2026-10-26: four
    meshes of 8 ranks all on CPUs 0-7). With too few idle CPUs the kernel
    schedules the ranks. Solves retain their existing scheduling. Explicit Hydra
    bindings and the DCDTUNI_MPI_BINDING=inherit opt-out are preserved. If
    affinity is unavailable or the requested rank count exceeds allowed CPUs,
    leave MPI's policy alone.
    """
    if not mesh_budget_active:
        return None
    env = dict(os.environ if base_environment is None else base_environment)
    if "HYDRA_BINDING" in env or env.get("DCDTUNI_MPI_BINDING", "auto") == "inherit":
        return None
    try:
        tokens = shlex.split(command)
        if not tokens or Path(tokens[0]).name != "mpirun":
            return None
        flag = next(i for i, word in enumerate(tokens) if word in ("-np", "-n"))
        ranks = int(tokens[flag + 1])
        allowed = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError, ValueError, StopIteration, IndexError):
        return None
    if ranks <= 0 or ranks > len(allowed):
        return None
    busy = _cpu_busy(allowed)
    claims = _claims_dir()
    taken = _claimed_by_others(claims, busy)
    primary, siblings, seen = [], [], set()
    for cpu in allowed:
        key = _core_key(cpu)
        if key in seen:
            siblings.append(cpu)
        else:
            primary.append(cpu)
            seen.add(key)
    free = [c for c in primary + siblings if c not in taken and busy.get(c, 0.0) <= _BUSY]
    if len(free) < ranks:
        return None
    chosen = free[:ranks]
    _write_claim(claims, chosen)
    env["HYDRA_BINDING"] = "user:" + ",".join(map(str, chosen))
    return env


def prepare_hwloc_plugins(project_root: Path | None = None, *,
                          source: Path = Path("/usr/lib/x86_64-linux-gnu/hwloc")) -> Path | None:
    """Exclude the X11-scanning GL plugin without creating folders outside the repo."""
    root = (project_root or Path(__file__).resolve().parents[4]).resolve()
    target = root / ".codex-tmp" / "hwloc_plugins_nogl"
    if not target.resolve().is_relative_to(root):
        raise RuntimeError("MPI plugin cache must remain inside the repository")
    if not source.is_dir():
        return target if target.is_dir() else None
    target.mkdir(parents=True, exist_ok=True)
    for plugin in sorted(source.glob("*.so")):
        if plugin.name == "hwloc_gl.so":
            continue
        try:
            (target / plugin.name).symlink_to(plugin)
        except FileExistsError:
            pass
    return target
