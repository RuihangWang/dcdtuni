"""T7 — dcCart memory preflight guard (runner side).

The whole cut-cell mesh is resident at once, so a dcCartMesh run whose leaf
count does not fit in RAM wedges the host (2026-09-18: 251 GB exhausted, SSH
lost). The runner refuses BEFORE the mesher starts when the configured
``maxGlobalCells`` cannot fit, and logs the estimate otherwise; the mesher
re-checks with the exact leaf count after its octree balance.

The RAM law is provisional (docs/developer/dccart-memory-profile-2026-09-21.md
plus the np1/np4/np16 samples of 2026-09-21): every rank duplicates the octree
and carve bookkeeping, so the total RISES with the rank count —

    total ≈ leaves × SHARED + ranks × (BASE + leaves × PER_RANK)

``maxGlobalCells`` is only a FLOOR on the octree's leaf count (the pass that
crosses it and the 2:1 balance still run, a whole level of overshoot), so the
runner's refusal uses a LOWER-bound constant and is therefore certain, while
its pass is advisory.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from loguru import logger as _loguru

from backend.aircooling.runner import stlgen_runner as sr
from backend.aircooling.runner.stlgen_runner import (
    MeshMemoryError,
    MeshTimeoutError,
    STLGenRunner,
)

GB = 1_000_000_000


@pytest.fixture
def logs():
    """Loguru sink: the runner logs through loguru, which caplog cannot see."""
    lines: list[str] = []
    sink_id = _loguru.add(lambda m: lines.append(m.record["message"]), level="INFO")
    try:
        yield lines
    finally:
        _loguru.remove(sink_id)


def _runner(mesh_config: dict | None = None, tmp_path: Path | None = None) -> STLGenRunner:
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._data = {"meshConfig": mesh_config if mesh_config is not None else {}}
    runner.mesh_dir = tmp_path or Path("/tmp")
    # These tests exercise memory/dispatch ordering without an exported
    # .cfdrun or STL upload; the provenance preflight has separate coverage.
    runner._verify_hybrid_source_stls = lambda: None
    return runner


# ---------------------------------------------------------------------------
# helpers: what "available" means
# ---------------------------------------------------------------------------

def test_mem_available_parses_MemAvailable_not_MemFree(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       263912788 kB\n"
        "MemFree:          8664324 kB\n"
        "MemAvailable:   194609648 kB\n"
        "Buffers:          1234 kB\n"
    )
    assert sr._mem_available_bytes(meminfo) == 194609648 * 1024
    meminfo.write_text("MemTotal: 1 kB\nMemFree: 8664324 kB\n")
    assert sr._mem_available_bytes(meminfo) is None
    assert sr._mem_available_bytes(tmp_path / "missing") is None


def test_cgroup_remaining_walks_ancestors_and_ignores_max(tmp_path):
    root = tmp_path / "cgroup"
    a = root / "a"
    b = a / "b"
    b.mkdir(parents=True)
    (root / "memory.max").write_text("max\n")
    (a / "memory.max").write_text("100000000\n")
    (a / "memory.current").write_text("25000000\n")
    (b / "memory.max").write_text("max\n")
    (b / "memory.current").write_text("1000\n")
    cg = tmp_path / "self_cgroup"
    cg.write_text("0::/a/b\n")
    assert sr._cgroup_mem_remaining_bytes(cg, root) == 75000000
    # a tighter child limit wins
    (b / "memory.max").write_text("50000000\n")
    (b / "memory.current").write_text("40000000\n")
    assert sr._cgroup_mem_remaining_bytes(cg, root) == 10000000
    # nothing limits us -> None
    (a / "memory.max").write_text("max\n")
    (b / "memory.max").write_text("max\n")
    assert sr._cgroup_mem_remaining_bytes(cg, root) is None
    assert sr._cgroup_mem_remaining_bytes(tmp_path / "missing", root) is None


def test_budget_env_override_wins(monkeypatch):
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, "123")
    assert sr._dcCart_mem_budget_bytes() == 123


def test_budget_is_the_smaller_of_meminfo_and_cgroup(monkeypatch):
    monkeypatch.delenv(sr.DCCART_MEM_AVAILABLE_ENV, raising=False)
    monkeypatch.setattr(sr, "_mem_available_bytes", lambda *a, **k: 200 * GB)
    monkeypatch.setattr(sr, "_cgroup_mem_remaining_bytes", lambda *a, **k: 50 * GB)
    assert sr._dcCart_mem_budget_bytes() == 50 * GB
    monkeypatch.setattr(sr, "_cgroup_mem_remaining_bytes", lambda *a, **k: None)
    assert sr._dcCart_mem_budget_bytes() == 200 * GB
    monkeypatch.setattr(sr, "_mem_available_bytes", lambda *a, **k: None)
    assert sr._dcCart_mem_budget_bytes() is None


# ---------------------------------------------------------------------------
# the law
# ---------------------------------------------------------------------------

def test_estimate_rises_with_the_rank_count():
    n = 329_179  # Slab octree leaves
    one = sr._dcCart_mem_estimate_bytes(n, 1)
    four = sr._dcCart_mem_estimate_bytes(n, 4)
    sixteen = sr._dcCart_mem_estimate_bytes(n, 16)
    assert one < four < sixteen
    # the per-rank slope is BASE + leaves x PER_RANK, times the safety factor
    slope = (sixteen - four) / 12
    expected = (sr.DCCART_MEM_RANK_BASE_BYTES + n * sr.DCCART_MEM_RANK_BYTES_PER_LEAF) * sr.DCCART_MEM_SAFETY
    assert abs(slope - expected) < 2


def test_estimate_fits_the_measured_points():
    """The constants must reproduce the profile's flagship point and this
    session's Slab samples to within the safety factor — never refuse a run
    that was measured to fit."""
    # Base604: 16.07M octree leaves, summed rank peaks 49.6 / 87.4 GB at
    # 8 / 16 ranks (2026-10-19)
    for n, measured in ((8, 49.6), (16, 87.4)):
        est_n = sr._dcCart_mem_estimate_bytes(16_069_870, n)
        assert measured * GB < est_n < measured * GB * 1.35, (n, est_n / GB)
    est = sr._dcCart_mem_estimate_bytes(16_069_870, 16)
    # Slab (1-s RSS samples, so the true peaks are a little higher):
    # 0.80 / 1.60 / 4.56 GB at 1 / 4 / 16 ranks
    for n, measured in ((1, 0.80), (4, 1.60), (16, 4.56)):
        est_n = sr._dcCart_mem_estimate_bytes(329_179, n)
        assert 0.9 * measured * GB < est_n < 1.35 * measured * GB, (n, est_n / GB)
    # the guard must not refuse Base604 on 16 ranks with the ~120 GB this
    # shared 251 GB host typically has available
    assert est + sr.DCCART_MEM_HEADROOM_BYTES < 120 * GB


def test_lower_bound_is_below_the_estimate():
    for n, p in ((500_000, 1), (5_000_000, 8), (20_000_000, 48)):
        assert sr._dcCart_mem_lower_bound_bytes(n, p) < sr._dcCart_mem_estimate_bytes(n, p)


def test_fit_leaves_inverts_the_estimate():
    budget = 100 * GB
    fit = sr._dcCart_mem_fit_leaves(budget, 16)
    assert fit % 100_000 == 0
    assert sr._dcCart_mem_estimate_bytes(fit, 16) + sr.DCCART_MEM_HEADROOM_BYTES <= budget
    assert sr._dcCart_mem_estimate_bytes(fit + 100_000, 16) + sr.DCCART_MEM_HEADROOM_BYTES > budget
    assert sr._dcCart_mem_fit_leaves(0, 16) == 0


def test_max_global_cells_shares_the_dicts_fallback():
    assert sr._dcCart_max_global_cells({}) == sr.DCCART_MAX_GLOBAL_CELLS_FALLBACK == 20_000_000
    assert sr._dcCart_max_global_cells({"maxGlobalCells": 0}) == 20_000_000
    assert sr._dcCart_max_global_cells({"maxGlobalCells": "6000000"}) == 6_000_000


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------

def test_guard_refuses_a_cap_that_cannot_fit(monkeypatch, tmp_path):
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(20 * GB))
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": 20_000_000}, tmp_path)
    with pytest.raises(MeshMemoryError) as exc:
        r._check_dcCart_memory(16)
    msg = str(exc.value)
    assert "maxGlobalCells" in msg and "20,000,000" in msg
    assert "16 rank" in msg
    assert "FEWER ranks" in msg
    assert f"{sr.DCCART_MEM_GUARD_ENV}=off" in msg
    assert "at most" in msg  # names the largest cap that fits
    assert not isinstance(exc.value, MeshTimeoutError)


def test_guard_passes_when_the_cap_fits(monkeypatch, tmp_path, logs):
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(200 * GB))
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": 5_000_000}, tmp_path)
    r._check_dcCart_memory(8)
    assert any("dcCart memory guard" in m and "estimate" in m for m in logs)


def test_guard_pass_is_advisory_refusal_is_certain(monkeypatch, tmp_path):
    """Between the lower bound and the estimate the guard must NOT refuse: the
    cap is a floor with a whole level of overshoot, so only a certain failure
    (lower bound > budget) is refused."""
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    cap, p = 6_000_000, 16
    low = sr._dcCart_mem_lower_bound_bytes(cap, p)
    est = sr._dcCart_mem_estimate_bytes(cap, p)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str((low + est) // 2))
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": cap}, tmp_path)
    r._check_dcCart_memory(p)  # no raise
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(low - 1))
    with pytest.raises(MeshMemoryError):
        r._check_dcCart_memory(p)


def test_guard_uses_the_dicts_20M_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(20 * GB))
    r = _runner({"meshEngine": "dcCart"}, tmp_path)
    with pytest.raises(MeshMemoryError, match="20,000,000"):
        r._check_dcCart_memory(4)


def test_guard_refusal_grows_with_ranks(monkeypatch, tmp_path):
    """More ranks need MORE memory on this mesher: a cap that fits on one rank
    can be refused on 48."""
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    cap = 10_000_000
    one = sr._dcCart_mem_lower_bound_bytes(cap, 1)
    many = sr._dcCart_mem_lower_bound_bytes(cap, 48)
    assert many > one
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str((one + many) // 2))
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": cap}, tmp_path)
    r._check_dcCart_memory(1)
    with pytest.raises(MeshMemoryError):
        r._check_dcCart_memory(48)


@pytest.mark.parametrize("value", ["off", "0", "false", "no", "OFF"])
def test_guard_env_off_skips(monkeypatch, tmp_path, value, logs):
    monkeypatch.setenv(sr.DCCART_MEM_GUARD_ENV, value)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, "1")
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": 20_000_000}, tmp_path)
    r._check_dcCart_memory(16)
    assert any("disabled" in m for m in logs)


def test_guard_skips_when_meminfo_unreadable(monkeypatch, tmp_path, logs):
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    monkeypatch.delenv(sr.DCCART_MEM_AVAILABLE_ENV, raising=False)
    monkeypatch.setattr(sr, "_dcCart_mem_budget_bytes", lambda: None)
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": 20_000_000}, tmp_path)
    r._check_dcCart_memory(16)
    assert any("unreadable" in m for m in logs)


def test_mesh_only_checks_memory_right_after_load(monkeypatch, tmp_path):
    """The guard runs before geometry validation, STL preparation and dict
    generation — the user sees the refusal in seconds and the previous mesh
    is untouched."""
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, "1")
    r = _runner({"meshEngine": "dcCart", "maxGlobalCells": 500_000,
                 "parallelMesh": True, "meshProcess": 4}, tmp_path)
    order: list[str] = []
    monkeypatch.setattr(r, "_preflight_case_path", lambda: order.append("case_path"))
    monkeypatch.setattr(r, "_load", lambda: order.append("load"))
    monkeypatch.setattr(r, "_preflight_floor_openings", lambda: order.append("floors"))
    monkeypatch.setattr(r, "validate_geometry", lambda: order.append("validate"))
    monkeypatch.setattr(r, "_generate_mesh_dicts", lambda: order.append("dicts"))
    with pytest.raises(MeshMemoryError):
        r.run_mesh_only()
    assert order == ["case_path", "load", "floors"]
    assert "validate" not in order and "dicts" not in order


def test_snappy_runs_are_not_guarded(monkeypatch, tmp_path):
    monkeypatch.delenv(sr.DCCART_MEM_GUARD_ENV, raising=False)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, "1")
    r = _runner({"meshEngine": "snappy", "maxGlobalCells": 20_000_000}, tmp_path)
    called = []
    monkeypatch.setattr(r, "_check_dcCart_memory", lambda n: called.append(n))
    monkeypatch.setattr(r, "_preflight_case_path", lambda: None)
    monkeypatch.setattr(r, "_load", lambda: None)
    monkeypatch.setattr(r, "_preflight_floor_openings", lambda: None)
    class _Stop(Exception):
        pass

    def _stop():
        raise _Stop

    monkeypatch.setattr(r, "validate_geometry", _stop)
    with pytest.raises(_Stop):
        r.run_mesh_only()
    assert called == []
