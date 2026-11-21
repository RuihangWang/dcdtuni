"""Process costs, the live process-tree meter, C0 charges and the CostLedger (CPU only)."""
from __future__ import annotations

import math
import subprocess
import sys
import time

import pytest

from agent.records import CostLedger, apply_c0, live_process_tree_costs, process_costs
from agent.records.costs import parse_stat, subtree


def burn(seconds=0.05):
    end = time.process_time() + seconds
    while time.process_time() < end:
        pass


def test_process_costs_fields_and_monotonic_cpu():
    first = process_costs()
    burn()
    second = process_costs()
    for key in ("process_wall_seconds", "process_own_cpu_seconds", "reaped_descendant_cpu_seconds",
                "total_process_tree_cpu_seconds"):
        assert math.isfinite(first[key]) and first[key] >= 0
    assert second["process_own_cpu_seconds"] > first["process_own_cpu_seconds"]
    assert second["total_process_tree_cpu_seconds"] == pytest.approx(
        second["process_own_cpu_seconds"] + second["reaped_descendant_cpu_seconds"])


def test_reaped_child_cpu_is_counted_after_wait():
    before = process_costs()["reaped_descendant_cpu_seconds"]
    subprocess.run([sys.executable, "-c", "import time\nend=time.process_time()+.2\nwhile time.process_time()<end:pass"],
                   check=True)
    assert process_costs()["reaped_descendant_cpu_seconds"] - before >= 0.15


def test_parse_stat_handles_spaces_in_comm_and_subtree():
    row = parse_stat("42 (a b) R 7 42 42 0 -1 0 0 0 0 0 11 13 17 19 20 0 1 0 555 0 0")
    assert row == {"pid": 42, "ppid": 7, "started_ticks": 555, "own_ticks": 24, "waited_descendant_ticks": 36}
    rows = {1: {"pid": 1, "ppid": 0}, 2: {"pid": 2, "ppid": 1}, 3: {"pid": 3, "ppid": 2}, 4: {"pid": 4, "ppid": 0}}
    assert [r["pid"] for r in subtree(rows, 1)] == [1, 2, 3]


def test_live_process_tree_costs_includes_this_process():
    cost = live_process_tree_costs()
    assert any(row["pid"] == __import__("os").getpid() for row in cost["live_process_nodes"])
    assert cost["total_process_tree_cpu_seconds"] > 0


def test_apply_c0_charges_once_and_keeps_unknown_dimensions_unknown():
    cost = {"process_wall_seconds": 10., "total_process_tree_cpu_seconds": 4., "GPU_residency_wall_seconds": None}
    charged = apply_c0(cost, [{"process_wall_seconds": 2., "total_process_tree_cpu_seconds": 1.}])
    assert charged["charged_process_wall_seconds"] == 12.
    assert charged["charged_total_process_tree_cpu_seconds"] == 5.
    assert charged["charged_GPU_residency_wall_seconds"] is None
    assert charged["unknown_historical_dimensions_not_zeroed"] is True


def test_ledger_charges_failed_attempts_and_c0_once():
    ledger = CostLedger(c0_charges=[{"process_wall_seconds": 1., "total_process_tree_cpu_seconds": 2.}])
    with ledger.attempt("ok-call", fidelity="lf") as record:
        burn()
        record["status"] = "succeeded"
    with pytest.raises(RuntimeError):
        with ledger.attempt("failing-call", fidelity="lf"):
            burn()
            raise RuntimeError("solver blew up")
    ledger.charge("external", {"stage_seconds": {"mesh": 3.5}}, status="failed")
    rows = ledger.attempts
    assert [row["label"] for row in rows] == ["ok-call", "failing-call", "external"]
    assert [row["status"] for row in rows] == ["succeeded", "failed", "failed"]
    assert rows[1]["error"].startswith("RuntimeError")
    for row in rows[:2]:
        assert row["costs"]["process_own_cpu_seconds"] > 0
    totals = ledger.totals()
    assert totals["attempts"] == 3 and totals["failed_attempts"] == 2
    assert totals["stage_seconds"] == pytest.approx(3.5)
    whole = ledger.whole()
    assert whole["charged_total_process_tree_cpu_seconds"] == pytest.approx(
        whole["total_process_tree_cpu_seconds"] + 2.)
    assert whole["C0_charges"] == ledger.c0_charges
    assert ledger.to_json()["format"] == "agent-cost-ledger/v1"
