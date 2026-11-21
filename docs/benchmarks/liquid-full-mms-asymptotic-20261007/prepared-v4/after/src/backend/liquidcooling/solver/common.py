"""Shared types and helpers for liquid cooling solvers."""

import csv
from dataclasses import dataclass
from pathlib import Path

import torch


@dataclass
class SolveLog:
    epoch: int
    total: float
    losses: dict[str, float]
    wall_time_s: float


def print_log(log: SolveLog) -> None:
    parts = [f"[Epoch {log.epoch:5d}] total={log.total:.4e}"]
    for k, v in sorted(log.losses.items()):
        parts.append(f"{k}={v:.2e}")
    parts.append(f"t={log.wall_time_s:.1f}s")
    print("  ".join(parts))


def save_loss_csv(logs: list[SolveLog], path: Path) -> None:
    if not logs:
        return
    all_keys: set[str] = set()
    for log in logs:
        all_keys.update(log.losses.keys())
    fieldnames = ["epoch", "total"] + sorted(all_keys) + ["wall_time_s"]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for log in logs:
            row: dict = {"epoch": log.epoch, "total": log.total,
                         "wall_time_s": log.wall_time_s}
            row.update(log.losses)
            writer.writerow(row)
