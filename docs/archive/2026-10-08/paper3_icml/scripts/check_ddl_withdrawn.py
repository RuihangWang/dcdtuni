#!/usr/bin/env python3
"""No phrase the manuscript withdrew may still be asserted by the code it cites.

`measure_e14.py` has carried a WITHDRAWN list since ledger row 35: phrases this campaign
retracted, which must not reappear in main.tex outside a quoted withdrawal.  It scanned
main.tex and nothing else, while the manuscript's own ledger rows cite the scripts under
`ddl/case/` as the evidence for the corrections -- so every withdrawn claim survived,
verbatim and asserted as fact, in the files a reader is pointed at:

  * `benchC/sensitivity_task.py` still opened by asserting that seven tasks reproduce
    "to one ULP (relative ~1.3e-13) at every thread count from 1 to 64".  Row (30)
    withdrew BOTH halves: the measurement is 6.4e-16, about 5 ULP, and no script in the
    repository performs a thread sweep at all;
  * `benchC/probe_reproducibility.py`, cited in the same row as the probe that exits 1
    printing "0 of 8 base objectives reproduce bitwise", still claimed to be
    "deterministic at every thread count from 1 to 32" and that "the disk cache is
    bitwise identical to a fresh compile" -- neither is measured by anything;
  * `benchC/E14_fullrank_search/run.py` and `benchC/E13_fullrank_ranking/run.py` still
    called the assignment block "full-rank", which row (41) withdrew because that block
    has exactly-zero Jacobian rows on the air-cooled half of its coordinates.

A correction that lands in the prose and not in the code it cites is half a correction, and
nothing was looking.  So this scans `ddl/case/**/*.py`.

Two exemptions exist and both are narrow and auditable:

  * a ``...'' span is a quotation of what was withdrawn, which is the manuscript's own
    convention and the same exemption `measure_e14.py` uses;
  * EXEMPT below, an explicit list of (file, phrase, context, reason).  Each entry must
    still match, so an exemption whose code has moved is reported as STALE and fails --
    the same shape as `check_no_number_lost.py`'s `--allow`, because a relaxation nobody
    can see is how a gate goes dead.

The scan fails when it finds nothing to scan.  A zero denominator has passed for nothing
six times in this campaign and this is not going to be the seventh.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
DDL_CASE = REPO / "ddl" / "case"

# The canonical list.  measure_e14.py imports it so that the manuscript scan and the code
# scan cannot drift apart -- one list, two call sites, which is the opposite of the two
# copies that let these phrases survive in the first place.
WITHDRAWN = [
    ("one ULP", "row 35: measured 6.4e-16, about 5 ULP, and the probe cited for it "
                "reports that no base objective reproduces bitwise"),
    ("thread count from", "row 35: no thread sweep exists in any script"),
    ("disk cache", "row 35: no script compares the compiled slot lengths to a cache"),
    ("every published E13 and E14", "row 36: the sensitivity script covers S-versus-R only"),
    ("full-rank", "row 41: the assignment block has exactly-zero Jacobian rows on half "
                  "its coordinates"),
    ("identical candidate pools", "row 34: each arm's pool comes from its own current design"),
]

# (path relative to the repository, phrase, context that must still be present, reason)
EXEMPT = [
    ("ddl/case/benchB/E11e_multiroom/fill_results.py", "full-rank",
     "full-rank either: its first-order response is exactly",
     "a NEGATION, and the sentence exists to retract the claim rather than make it.  It "
     "is also inside the `note=` string that fill_results.py emits into "
     "gen_appendix.tex, so rewording it changes a generated, committed file and the "
     "appendix freshness gate; it stays as written and is exempted by name."),
]

QUOTE = re.compile(r"``.*?''", re.S)


def fold(src: str) -> str:
    """Quote-stripped, whitespace-folded and lower-cased: the form matching runs on.

    Case-insensitive on purpose.  main.tex withdraws the claim as ```Full-rank'' was
    asserted for a block whose Jacobian has exactly-zero rows'', capitalised, and a
    case-sensitive scan would have let the capitalised spelling through in the code too --
    which is how a phrase gets "corrected" into a variant of itself and survives.
    """
    return re.sub(r"\s+", " ", QUOTE.sub(lambda m: " " * len(m.group(0)), src)).lower()


def scan_source(rel: str, src: str, problems: list, notes: list):
    """One file.  `src` is the raw text; matching runs on `fold(src)` so a phrase wrapped
    across two source lines is still found."""
    folded = fold(src)
    for phrase, why in WITHDRAWN:
        folded_phrase = re.sub(r"\s+", " ", phrase).lower()
        if folded_phrase not in folded:
            continue
        exempted = None
        for path, ephrase, context, reason in EXEMPT:
            if path != rel or ephrase != phrase:
                continue
            if re.sub(r"\s+", " ", context).lower() in folded:
                exempted = reason
                break
        if exempted:
            notes.append(f"{rel}: {phrase!r} exempt -- {exempted}")
            continue
        # Report a line number against the RAW text, which is what a reader will open.  It
        # is a hint, not a claim: a phrase wrapped across two lines points at the first.
        first = folded_phrase.split()[0]
        line = next((i for i, ln in enumerate(src.splitlines(), 1)
                     if first in ln.lower()), None)
        problems.append(f"{rel}" + (f":{line}" if line else "") +
                        f" still asserts {phrase!r} ({why}); the manuscript withdrew it, "
                        f"so the code the ledger cites as evidence contradicts the "
                        f"correction it is cited for")


def scan_tree(root: Path, problems: list, notes: list, limit=None):
    """Every .py under `root`.  Returns the number of files scanned."""
    if not root.is_dir():
        problems.append(f"{root} is not a directory, so the withdrawn-phrase scan over the "
                        f"case code had nothing to scan and cannot report OK")
        return 0
    files = sorted(p for p in root.rglob("*.py")
                   if "__pycache__" not in p.parts and ".worktrees" not in p.parts)
    if limit:
        files = [p for p in files if any(str(p).endswith(x) for x in limit)]
    for p in files:
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            problems.append(f"{p.relative_to(REPO)} could not be read: {e}")
            continue
        scan_source(str(p.relative_to(REPO)), src, problems, notes)
    return len(files)


def stale_exemptions(problems: list, scanned: set):
    """An exemption whose file or context has moved is a relaxation nobody can see."""
    for path, phrase, context, _reason in EXEMPT:
        p = REPO / path
        if path not in scanned:
            problems.append(f"STALE exemption: {path} was not scanned (it no longer exists "
                            f"or is outside the scan root), so the {phrase!r} exemption "
                            f"covers nothing and must be removed")
            continue
        folded = fold(p.read_text(encoding="utf-8", errors="replace"))
        if re.sub(r"\s+", " ", context).lower() not in folded:
            problems.append(f"STALE exemption: {path} no longer contains the context the "
                            f"{phrase!r} exemption was granted for, so either the code "
                            f"moved or the exemption is now hiding something")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DDL_CASE),
                    help="directory to scan (default: ddl/case)")
    ap.add_argument("--limit", nargs="*", default=None,
                    help="only scan files whose path ends with one of these")
    args = ap.parse_args()

    if not WITHDRAWN:
        print("WITHDRAWN-PHRASE GATE FAILED: the phrase list is empty, so the scan had "
              "nothing to look for", file=sys.stderr)
        return 1

    problems, notes = [], []
    root = Path(args.root)
    n = scan_tree(root, problems, notes, args.limit)
    scanned = set()
    for path, _p, _c, _r in EXEMPT:
        if (REPO / path).exists():
            scanned.add(path)
    stale_exemptions(problems, scanned)

    for x in notes:
        print("  note: " + x)
    print(f"WITHDRAWN-PHRASE GATE: {n} files under {root.relative_to(REPO)}, "
          f"{len(WITHDRAWN)} withdrawn phrases, {len(EXEMPT)} named exemption(s), "
          f"{len(notes)} occurrence(s) exempted")
    if problems:
        print("WITHDRAWN-PHRASE GATE FAILED:", file=sys.stderr)
        for x in problems:
            print("  -", x, file=sys.stderr)
        return 1
    print("WITHDRAWN-PHRASE GATE OK (no withdrawn phrase is asserted by the case code)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
