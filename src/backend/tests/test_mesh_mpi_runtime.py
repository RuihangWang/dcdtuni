"""Mesh affinity must stay within the host allowance and preserve user policy."""
from pathlib import Path
import pytest

from backend.aircooling.runner import mpi_runtime


def _affinity(monkeypatch, cpus, busy=None, claims_dir=None):
    monkeypatch.setattr(mpi_runtime.os, "sched_getaffinity", lambda _: set(cpus))
    monkeypatch.setattr(mpi_runtime, "_core_key", lambda cpu: ("cpu", cpu))
    monkeypatch.setattr(mpi_runtime, "_cpu_busy", lambda cpus: dict(busy or {}))
    monkeypatch.setattr(mpi_runtime, "_claims_dir", lambda: claims_dir)


def test_mesh_binds_each_rank_to_a_distinct_allowed_cpu(monkeypatch):
    _affinity(monkeypatch, [15, 9, 8, 12, 13, 11, 10, 14])
    env = mpi_runtime.mesh_mpi_environment("mpirun -np 8 dcCartMesh -parallel",
        mesh_budget_active=True, base_environment={"PATH": "/configured"})
    assert env == {"PATH": "/configured", "HYDRA_BINDING": "user:8,9,10,11,12,13,14,15"}


def test_physical_cores_precede_smt_siblings(monkeypatch):
    _affinity(monkeypatch, range(6))
    monkeypatch.setattr(mpi_runtime, "_core_key", lambda cpu: (0, cpu // 2))
    env = mpi_runtime.mesh_mpi_environment("/opt/mpi/bin/mpirun -n 4 checkMesh",
        mesh_budget_active=True, base_environment={})
    assert env["HYDRA_BINDING"] == "user:0,2,4,1"


def test_busy_cpus_are_skipped(monkeypatch):
    # Another user's job (or this host's other mesh) keeps 0-3 busy: binding
    # there stacked every concurrent mesh on the same cores (Base604, 2026-10-26).
    _affinity(monkeypatch, range(8), busy={0: 0.9, 1: 0.95, 2: 0.8, 3: 1.0})
    env = mpi_runtime.mesh_mpi_environment("mpirun -np 4 dcCartMesh -parallel",
        mesh_budget_active=True, base_environment={})
    assert env["HYDRA_BINDING"] == "user:4,5,6,7"


def test_too_few_idle_cpus_leaves_scheduling_to_the_kernel(monkeypatch):
    _affinity(monkeypatch, range(4), busy={0: 0.9, 1: 0.9})
    assert mpi_runtime.mesh_mpi_environment("mpirun -np 4 dcCartMesh",
        mesh_budget_active=True, base_environment={}) is None


def test_concurrent_meshes_claim_disjoint_cpus(monkeypatch, tmp_path):
    # Two servers starting together see the same idle CPUs; the first one's
    # claim keeps the second off them.
    _affinity(monkeypatch, range(8), claims_dir=tmp_path)
    monkeypatch.setattr(mpi_runtime.os, "getpid", lambda: 1)
    monkeypatch.setattr(mpi_runtime, "_pid_alive", lambda pid: True)
    first = mpi_runtime.mesh_mpi_environment("mpirun -np 4 dcCartMesh",
        mesh_budget_active=True, base_environment={})
    monkeypatch.setattr(mpi_runtime.os, "getpid", lambda: 2)
    second = mpi_runtime.mesh_mpi_environment("mpirun -np 4 dcCartMesh",
        mesh_budget_active=True, base_environment={})
    assert first["HYDRA_BINDING"] == "user:0,1,2,3"
    assert second["HYDRA_BINDING"] == "user:4,5,6,7"


def test_claims_of_dead_or_idle_servers_are_ignored(monkeypatch, tmp_path):
    _affinity(monkeypatch, range(4), claims_dir=tmp_path)
    (tmp_path / "7").write_text("0 1 2 3\n")
    import os as _os
    old = 1_000_000_000
    _os.utime(tmp_path / "7", (old, old))
    monkeypatch.setattr(mpi_runtime, "_pid_alive", lambda pid: pid != 9)
    (tmp_path / "9").write_text("0 1 2 3\n")
    monkeypatch.setattr(mpi_runtime.os, "getpid", lambda: 1)
    env = mpi_runtime.mesh_mpi_environment("mpirun -np 4 dcCartMesh",
        mesh_budget_active=True, base_environment={})
    # pid 7 is alive but its claim is old and its CPUs are idle; pid 9 is dead.
    assert env["HYDRA_BINDING"] == "user:0,1,2,3"
    assert not (tmp_path / "9").exists()


@pytest.mark.parametrize("environment", [{"HYDRA_BINDING": "none"},
    {"HYDRA_BINDING": "user:12,13"}, {"DCDTUNI_MPI_BINDING": "inherit"}])
def test_explicit_user_policy_is_never_overridden(monkeypatch, environment):
    _affinity(monkeypatch, [8, 9])
    original = dict(environment)
    assert mpi_runtime.mesh_mpi_environment("mpirun -np 2 dcCartMesh",
        mesh_budget_active=True, base_environment=environment) is None
    assert environment == original


def test_solver_schedule_is_unchanged(monkeypatch):
    _affinity(monkeypatch, [8, 9])
    assert mpi_runtime.mesh_mpi_environment("mpirun -np 2 buoyantBoussinesqSimpleFoam_new",
        mesh_budget_active=False, base_environment={}) is None


@pytest.mark.parametrize("command", ["mpirun -np 3 dcCartMesh", "mpirun -np 0 dcCartMesh",
    "mpirun -np bad dcCartMesh", "mpirun", "checkMesh"])
def test_invalid_or_oversubscribed_commands_keep_inherited_policy(monkeypatch, command):
    _affinity(monkeypatch, [8, 9])
    assert mpi_runtime.mesh_mpi_environment(command,
        mesh_budget_active=True, base_environment={}) is None


def test_unavailable_affinity_keeps_inherited_policy(monkeypatch):
    def unavailable(_):
        raise OSError("unavailable")
    monkeypatch.setattr(mpi_runtime.os, "sched_getaffinity", unavailable)
    assert mpi_runtime.mesh_mpi_environment("mpirun -np 2 dcCartMesh",
        mesh_budget_active=True, base_environment={}) is None


def test_hwloc_cache_is_repository_local_and_idempotent(tmp_path):
    root, source = tmp_path / "repo", tmp_path / "plugins"
    root.mkdir(); source.mkdir()
    for name in ("hwloc_gl.so", "hwloc_xml.so", "hwloc_cuda.so"):
        (source / name).write_text("plugin")
    cache = mpi_runtime.prepare_hwloc_plugins(root, source=source)
    assert cache == root / ".codex-tmp/hwloc_plugins_nogl"
    assert sorted(p.name for p in cache.iterdir()) == ["hwloc_cuda.so", "hwloc_xml.so"]
    assert all(p.is_symlink() for p in cache.iterdir())
    assert mpi_runtime.prepare_hwloc_plugins(root, source=source) == cache


def test_hwloc_cache_rejects_a_directory_escape(tmp_path):
    root, outside = tmp_path / "repo", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (root / ".codex-tmp").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="inside the repository"):
        mpi_runtime.prepare_hwloc_plugins(root, source=tmp_path / "not-installed")
