#!/usr/bin/env python3
"""Gate the E11e multi-room replication's numbers, and its verdict, against its record.

`records/e11e_24tasks.json` is `score_e11e.py` run over the three live room logdirs.  It exists
because the replication finished after the appendix snapshot was pinned: all 24 pre-registered
tasks have been run, the body's earlier reason for the gate being unmet ("the remaining tasks
have not been run") became false, and a number typed into the body to replace it would have been
the eleventh prose-carried number this campaign got wrong.

The clause that matters is structural, not numeric.  The pre-registered gate has TWO parts --
at least 20 of 24 wins AND no uninformative task.  The replication satisfies the first
comfortably (23 of 23 informative, p = 1.19e-07) and fails the second (one uninformative task),
so the gate is not met.  A paper that quoted the win count and dropped the verdict would be
telling the truth about a number and lying about the experiment, which is why this gate fails if
either half is missing from the text.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def claim_scoped(src, specs, problems, where):
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for {label}, "
                            "so that number is unchecked -- the prose moved or the anchor is wrong, "
                            "and a check with nothing to check is not a pass")
            continue
        if isinstance(expected, tuple):
            want = tuple(str(e) for e in expected)
            bad = [h for h in hits if tuple(h) != want]
            shown = " / ".join(want)
        else:
            ok = {str(expected)}
            flat = [h if isinstance(h, str) else " / ".join(h) for h in hits]
            bad = [h for h in flat if h not in ok]
            shown = ", ".join(sorted(ok)[:4])
        if bad:
            problems.append(f"{where}: {len(bad)} of {len(hits)} statements of {label} disagree "
                            f"with the record: {sorted(set(map(str, bad)))[:4]} against {shown}")
        else:
            print(f"  {where}: {label} -- all {len(hits)} statement(s) agree with the record")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", default=str(HERE / "records" / "e11e_24tasks.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp = Path(args.record)
    if not rp.exists():
        print(f"FAIL: no E11e record at {rp}; the replication numbers in the body would be "
              "unchecked. Regenerate with score_e11e.py over the three room logdirs.",
              file=sys.stderr)
        return 2
    rec = json.loads(rp.read_text())
    src = Path(args.tex).read_text()
    gen = (HERE / "gen_appendix.tex")
    gen_src = gen.read_text() if gen.exists() else ""
    problems = []

    grid = (rec.get("grids") or {}).get(rec.get("deciding_grid") or "pooled_percentiles_PRIMARY") \
        or (rec.get("grids") or {}).get("pooled_percentiles_PRIMARY") or {}
    wins = grid.get("wins")
    n_inf = grid.get("n_informative")
    n_uninf = grid.get("n_uninformative")
    p = grid.get("p")
    gate = rec.get("gate_20_of_24")
    n_recs = rec.get("n_task_records")
    per_room = grid.get("per_room") or {}

    if wins is None or n_inf is None or p is None:
        problems.append("e11e: the record carries no wins/n_informative/p for the deciding grid, "
                        "so there is nothing to gate against")
        return 1 if problems else 0

    # ---- the structural clause -------------------------------------------------
    clears_count = wins >= 20
    if gate is False and clears_count:
        # The win criterion is met and the gate is not.  Both must be in the text.
        if "still not met" not in src and "not met" not in src:
            problems.append("e11e: the gate is NOT met (one uninformative task) while the win "
                            f"count clears 20/24 at {wins}/{n_inf}, and main.tex does not say the "
                            "gate is unmet -- quoting the win without the verdict is the failure "
                            "mode this gate exists to catch")
        if "uninformative" not in src:
            problems.append("e11e: the gate is unmet because of an uninformative task and "
                            "main.tex never uses the word")
    if gate is True and "not met" in src:
        problems.append("e11e: the record says the gate IS met but main.tex still says it is not")

    # ---- the appendix snapshot lags the campaign; both counts must be visible ----
    m = re.search(r"over (\d+) tasks in (\d+) rooms", gen_src)
    snap = m.group(1) if m else None
    if snap and n_recs and int(snap) != int(n_recs):
        if f"pinned at ${snap}$ tasks" not in src.replace("\n", " ").replace("  ", " "):
            if not re.search(r"pinned at\s*\$" + snap + r"\$\s*tasks", src):
                problems.append(f"e11e: the appendix snapshot scores {snap} tasks while the live "
                                f"records hold {n_recs}, and main.tex does not disclose the lag")

    n_rooms = len(per_room) if per_room else None
    per_room_n = sorted({v.get("tasks") for v in per_room.values()}) if per_room else []

    specs = [
        (r"replication of \$3\\times8 = (\d+)\$ tasks, and all \$(\d+)\$\s*have now been run, "
         r"\$(\d+)\$ per room template",
         (n_recs, n_recs, per_room_n[0] if len(per_room_n) == 1 else None),
         "the replication's task count and its per-room count"),
        (r"It wins \$(\d+)\$ of the \$(\d+)\$ informative tasks at\s*one-sided exact "
         r"\$p = ([\d.]+)\\times10\^\{-7\}\$", (wins, n_inf, f"{p * 1e7:.2f}"),
         "the replication's win count and exact sign-test p"),
        (r"clears the pre-registered \$\\ge (\d+)/(\d+)\$", (20, n_recs),
         "the pre-registered win threshold"),
        (r"snapshot\s*pinned at \$(\d+)\$ tasks", snap,
         "the count the appendix snapshot is pinned at"),
    ]
    specs = [s for s in specs if s[1] is not None and
             (not isinstance(s[1], tuple) or all(x is not None for x in s[1]))]
    claim_scoped(src, specs, problems, "e11e")

    if n_uninf:
        print(f"  e11e: {n_uninf} uninformative task(s) -- the second clause of the gate, and the "
              "reason it is not met despite the win count clearing")
    if problems:
        print("E11E GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print(f"e11e numbers agree with {rp.name} (win count AND gate verdict)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
