#!/usr/bin/env python3
"""Fail if the committed gen_appendix.tex is not what the records generate now.

This is the gate for the defect class that has now bitten this manuscript three times,
each time in the same direction -- hiding a result that had already landed:

  * the submitted appendix printed "D1 (pre-registered gate) PENDING: no
    results_e11d_reeval.json on disk yet" while that record had been on disk for 90
    minutes saying the gate FAILED (ledger row 27);
  * the body said "the three-room version is in the appendix" while the appendix said
    "over 2 tasks in 1 rooms", and the records held 8 across 3;
  * and after row (27) was written to say the generator "reads the records at build time
    and has no cached copy, so a stale appendix is now a build step that was skipped
    rather than a state that can persist silently", the state persisted silently again:
    the shipped PDF said "8 tasks in 3 rooms, 8/8 wins, p = 0.003906" while 10 task
    records were on disk at commit time.

Row (27)'s claim was itself false, and that is the lesson: `gen_appendix.tex` IS a
committed cached copy, the Makefile never invoked `fill_results.py`, and nothing compared
the two.  So the comparison now exists, and it is a gate rather than a discipline.

Non-destructive by construction: the generator is run against a sandbox whose `docs/...`
is a real empty directory (so it writes there and cannot touch the committed file).  The
verdict is a diff, and the diff is printed, because "stale" without the delta is not
actionable.

The sandbox is also HERMETIC, and that is a correction rather than a refinement.  `ddl`
used to be a symlink to the live tree, so the generator could read the gitignored
`loggers/` directories through it: three of its inputs (E11b's lambda bracket, its
objective-coupling comparison and its S-versus-R sweep) came from `loggers/derive/
review_numbers.json` by path rather than through `fill_results.py`'s own
snapshot-only `resolve()`.  The gate was therefore green on the author's machine and red
on a clean clone, where it reported six lines of drift no rebuild could remove -- a gate
that can only pass on a machine that happens to hold untracked data is not a gate.  `ddl`
is now a real EMPTY directory: the generator can only see `records/campaign_snapshot/`,
so this gate certifies that a checkout of this revision regenerates this appendix.
"""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
FILL = REPO / "ddl" / "case" / "benchB" / "E11e_multiroom" / "fill_results.py"
COMMITTED = HERE / "gen_appendix.tex"


def main() -> int:
    if not FILL.exists():
        print(f"appendix freshness gate: generator not found at {FILL}", file=sys.stderr)
        return 2
    if not COMMITTED.exists():
        print(f"appendix freshness gate: {COMMITTED.name} is not committed", file=sys.stderr)
        return 1

    sandbox = Path(tempfile.mkdtemp(prefix="appendix_fresh."))
    try:
        # A real empty directory, NOT a symlink to REPO/"ddl".  See the module docstring:
        # the symlink let the generator read gitignored loggers and made this gate
        # machine-dependent.  An empty `ddl` means any future live-tree read shows up
        # here as a drift finding instead of passing on the author's machine only.
        (sandbox / "ddl").mkdir()
        out_dir = sandbox / "docs" / "manuscripts" / "paper3_icml"
        out_dir.mkdir(parents=True)
        # The generator reads the COMMITTED record snapshot, not the live gitignored
        # loggers, so the sandbox has to see records/ as well.  Without this link the
        # gate fails for the wrong reason -- no snapshot -- rather than on staleness.
        # records/ is TRACKED, so linking it costs no hermeticity.
        (out_dir / "records").symlink_to(HERE / "records", target_is_directory=True)
        # Assert the sandbox really is hermetic before trusting the verdict: a gate that
        # can see untracked data cannot fail for the right reason.
        leaked = sorted(p.name for p in (sandbox / "ddl").rglob("*"))
        if leaked:
            print(f"appendix freshness gate: the sandbox is not hermetic, it can see "
                  f"{leaked[:5]}", file=sys.stderr)
            return 2
        r = subprocess.run([sys.executable, str(FILL), str(sandbox)],
                           capture_output=True, text=True, cwd=str(REPO))
        gen = out_dir / "gen_appendix.tex"
        if not gen.exists():
            print("appendix freshness gate: the generator produced nothing\n"
                  f"  stdout: {r.stdout[-600:]}\n  stderr: {r.stderr[-600:]}",
                  file=sys.stderr)
            return 2
        a = COMMITTED.read_text(encoding="utf-8").splitlines()
        b = gen.read_text(encoding="utf-8").splitlines()
        if a == b:
            print(f"appendix freshness gate OK ({len(a)} lines, identical to what the "
                  f"committed record snapshot generates in a hermetic sandbox)")
            return 0
        diff = list(difflib.unified_diff(a, b, "committed/gen_appendix.tex",
                                         "generated-from-records", lineterm="", n=1))
        changed = sum(1 for l in diff if l.startswith(("+", "-"))
                      and not l.startswith(("+++", "---")))
        print(f"appendix freshness gate FAILED: the committed gen_appendix.tex differs "
              f"from what the records generate now in {changed} lines.\n"
              f"Re-run: python {FILL.relative_to(REPO)} \"$PWD\" (from the repository "
              f"root), then rebuild and commit.\n", file=sys.stderr)
        for line in diff[:60]:
            print("  " + line, file=sys.stderr)
        if len(diff) > 60:
            print(f"  ... {len(diff) - 60} more diff lines", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
