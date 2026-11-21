"""Measured costs: the process meter, the live process-tree meter, C0 charges and the CostLedger.

``process_costs`` and ``live_process_tree_costs``/``apply_c0`` are the frozen
``training/process_costs.py`` and ``training/main_study_costs.py`` with the
same fields and semantics:

- wall seconds since this process was born, its own CPU, and the CPU of
  descendants it has already reaped (``RUSAGE_CHILDREN``), so a solver run by a
  private server counts once the server has been stopped and waited;
- the live meter sums every live process of the tree from ``/proc`` between
  two identical membership snapshots, and refuses a racing tree instead of
  returning a partial cost;
- ``apply_c0`` adds historical one-off setup charges (C0) to a whole-process
  cost once, and keeps a dimension unknown (``None``) rather than zero when any
  piece is unknown.

:class:`CostLedger` books every attempt, failures included, at what it really
cost: a failed or interrupted call is charged like a successful one, never
amortised over the successes, and C0 enters the whole-run total exactly once.
GPU time is not measured here (reported as ``None``).
"""
from __future__ import annotations

import os
import resource
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

LEDGER_FORMAT = "agent-cost-ledger/v1"
_DELTA_KEYS = ("process_wall_seconds", "process_own_cpu_seconds", "reaped_descendant_cpu_seconds",
               "total_process_tree_cpu_seconds")


def process_costs() -> Dict[str, Any]:
    """Linux process-wide wall/CPU accounting, including reaped descendants."""
    # comm may contain spaces; fields after the closing ')' start at field 3.
    values = Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()
    started = float(values[19]) / os.sysconf("SC_CLK_TCK")
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"process_wall_seconds": time.clock_gettime(time.CLOCK_BOOTTIME) - started,
            "process_own_cpu_seconds": own.ru_utime + own.ru_stime,
            "reaped_descendant_cpu_seconds": children.ru_utime + children.ru_stime,
            "total_process_tree_cpu_seconds": own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime,
            "measurement": "Linux process birth through final checkpoint/worker shutdown; includes imports, "
                           "setup and waited descendants",
            "live_children_requirement": "all evaluation workers stopped and reaped before final meter"}


def parse_stat(text: str) -> Dict[str, int]:
    pid = int(text.split(" ", 1)[0])
    values = text.rsplit(") ", 1)[1].split()
    return {"pid": pid, "ppid": int(values[1]), "started_ticks": int(values[19]),
            "own_ticks": int(values[11]) + int(values[12]),
            "waited_descendant_ticks": int(values[13]) + int(values[14])}


def subtree(records: Dict[int, Dict[str, int]], root: int) -> List[Dict[str, int]]:
    chosen = {root}
    changed = True
    while changed:
        changed = False
        for pid, row in records.items():
            if row["ppid"] in chosen and pid not in chosen:
                chosen.add(pid)
                changed = True
    return [records[pid] for pid in sorted(chosen) if pid in records]


def _snapshot(root: int) -> List[Dict[str, int]]:
    records = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            row = parse_stat((directory / "stat").read_text())
        except (OSError, ValueError, IndexError):
            continue
        records[row["pid"]] = row
    return subtree(records, root)


def live_process_tree_costs(root: Optional[int] = None) -> Dict[str, Any]:
    """Call after completed physics responses (and CUDA synchronization, if any).

    Sum each currently live process's own CPU plus its already-waited child
    CPU. Ancestors have not yet counted live descendants, so nothing is counted
    twice. Two matching membership/start/parent snapshots guard against child
    races. At final shutdown use :func:`process_costs` as the truth.
    """
    root = root or os.getpid()
    ticks = os.sysconf("SC_CLK_TCK")

    def identity(rows):
        return [(r["pid"], r["started_ticks"], r["ppid"]) for r in rows]
    for _ in range(5):
        before, after = _snapshot(root), _snapshot(root)
        if identity(before) == identity(after) and any(r["pid"] == root for r in after):
            rootrow = next(r for r in after if r["pid"] == root)
            return {"process_wall_seconds": time.clock_gettime(time.CLOCK_BOOTTIME) - rootrow["started_ticks"] / ticks,
                    "total_process_tree_cpu_seconds": sum(r["own_ticks"] + r["waited_descendant_ticks"]
                                                          for r in after) / ticks,
                    "live_process_nodes": after, "clock_tick_seconds": 1 / ticks,
                    "measurement": "quiescent /proc own+already-waited descendants; stable membership bracket; "
                                   "final resource meter required"}
    raise RuntimeError("process-tree membership raced at the checkpoint; retain failure rather than partial cost")


def apply_c0(cost: Dict[str, Any], charges: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """``cost`` plus one-off C0 charges per dimension; unknown stays unknown."""
    result = dict(cost)
    for dimension in ("process_wall_seconds", "total_process_tree_cpu_seconds", "GPU_residency_wall_seconds"):
        pieces = [c.get(dimension) for c in charges]
        result["charged_" + dimension] = (None if cost.get(dimension) is None or any(v is None for v in pieces)
                                          else float(cost[dimension]) + sum(pieces))
    result["C0_charges"] = list(charges)
    result["unknown_historical_dimensions_not_zeroed"] = True
    return result


def cost_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, float]:
    """``after - before`` for the four process-meter dimensions."""
    return {key: float(after[key]) - float(before[key]) for key in _DELTA_KEYS}


class CostLedger:
    """Every attempt at its measured cost; failures are charged; C0 once in :meth:`whole`."""

    def __init__(self, *, c0_charges: Sequence[Dict[str, Any]] = ()):
        self.c0_charges: List[Dict[str, Any]] = [dict(c) for c in c0_charges]
        self.started = process_costs()
        self.attempts: List[Dict[str, Any]] = []

    @contextmanager
    def attempt(self, label: str, **fields: Any) -> Iterator[Dict[str, Any]]:
        """Meter the body; the yielded record may set ``status`` and add fields. Always booked."""
        record: Dict[str, Any] = {"label": label, **fields, "status": "completed"}
        before = process_costs()
        try:
            yield record
        except BaseException as exc:
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["costs"] = cost_delta(before, process_costs())
            self.attempts.append(record)

    def charge(self, label: str, costs: Dict[str, Any], *, status: str, **fields: Any) -> Dict[str, Any]:
        """Book an externally measured cost (e.g. a ``result.json``'s stage seconds)."""
        if not isinstance(costs, dict) or not costs:
            raise ValueError("a charge needs its measured costs")
        record = {"label": label, **fields, "status": status, "costs": dict(costs)}
        self.attempts.append(record)
        return record

    def totals(self) -> Dict[str, Any]:
        """Sums over all attempts (failed ones included), separately for metered and reported costs."""
        def total(key):
            return sum(float(row["costs"].get(key, 0.0)) for row in self.attempts
                       if isinstance(row["costs"].get(key, 0.0), (int, float)))
        stage_seconds = 0.0
        for row in self.attempts:
            stages = row["costs"].get("stage_seconds") or {}
            stage_seconds += sum(float(v) for v in stages.values() if isinstance(v, (int, float)))
        return {"attempts": len(self.attempts),
                "failed_attempts": sum(row["status"] not in ("completed", "succeeded") for row in self.attempts),
                **{key: total(key) for key in _DELTA_KEYS}, "stage_seconds": stage_seconds,
                "GPU_seconds": None}

    def whole(self) -> Dict[str, Any]:
        """The whole-process meter now, with the C0 charges applied once."""
        return apply_c0(process_costs(), self.c0_charges)

    def to_json(self) -> Dict[str, Any]:
        return {"format": LEDGER_FORMAT, "started": self.started, "attempts": self.attempts,
                "totals": self.totals(), "whole": self.whole(), "c0_charges": self.c0_charges}
