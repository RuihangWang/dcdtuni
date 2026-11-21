#!/usr/bin/env python3
"""Poison the prose-count gate; every case must exit non-zero.

The gate this replaces was dead, and the only reason anyone knows is that a review ran it
against a document it could not match.  So the replacement is demonstrated against the five
ways a count goes wrong, including the two the review demonstrated on the dead one.

THIS FILE WAS ITSELF DEAD, in the second sense: it had no call site, so nothing noticed
that three of its six anchors had rotted.  They were literals transcribed from a document
that has since grown -- `thirty-two defects` when the ledger has forty-two rows,
`"\\n32 & "` when the last row is 42, `Sixteen of the thirty-two---(7)` when the enumerated
list now says nineteen of the forty-two, `(part 6 of 6: rows 29--32)` when there are nine
parts.  Each `.replace()` silently did nothing, the poisoned document equalled the
unpoisoned one, the gate passed, and the harness printed MISSED -- which reads as "the gate
is weak" when the truth is "the poison was never applied".  A poison that cannot be applied
is not a weak gate, it is no gate at all, so every anchor here is now DERIVED from the
document: the ledger's own highest row number, the gate's own number-word parser, the parts
caption that is actually there.  A derivation that finds nothing raises, and a poison whose
text equals the unpoisoned text raises, so this harness cannot report a false CAUGHT or a
misleading MISSED.

Wired into `scripts/check_manuscript.py`, because a poison test nobody runs is the
dead-check defect in a new hat.
"""
from __future__ import annotations
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(sys.argv[1])
sys.path.insert(0, str(R / "scripts"))
from check_prose_counts import UNITS, TENS, NUM, NUMWORD, to_int   # noqa: E402

BASE = (R / "main.tex").read_text()
GATE = R / "scripts" / "check_prose_counts.py"

UNITS_INV = {v: k for k, v in UNITS.items()}
TENS_INV = {v: k for k, v in TENS.items()}
# The gate's own number-word pattern, so this harness cannot drift from the thing it
# poisons: NUMWORD matches "forty-two" and "nineteen", NUM adds the digits.


def word(n: int) -> str:
    """42 -> 'forty-two': the inverse of the gate's own number-word parser."""
    if n < 0 or n > 99:
        raise SystemExit(f"HARNESS FAILURE: cannot write {n} as a number word")
    if n < 20:
        return UNITS_INV[n]
    t, u = divmod(n, 10)
    return TENS_INV[t * 10] + ("" if u == 0 else "-" + UNITS_INV[u])


def find(pattern, what, flags=0):
    ms = list(re.finditer(pattern, BASE, flags))
    if not ms:
        raise SystemExit(f"HARNESS FAILURE: cannot locate {what} ({pattern!r}) in "
                         f"main.tex, so that poison cannot be applied; the prose moved and "
                         f"the anchor has to be re-derived")
    return ms


# ---- anchors, all derived from the document that is actually there --------------------
ROWS = sorted({int(m.group(1)) for m in re.finditer(r"^(\d{1,2}) & ", BASE, re.M)})
if not ROWS:
    raise SystemExit("HARNESS FAILURE: no ledger rows `^N & ` in main.tex")
N_ROWS, LAST_ROW = len(ROWS), ROWS[-1]
TOTAL = word(N_ROWS)
_i = BASE.rindex(f"\n{LAST_ROW} & ")
LAST_ROW_TEXT = BASE[_i:BASE.index("\\\\\n", _i) + 3]
LAST_PART = find(r"\(part \d+ of \d+: rows \d+--\d+\)", "a ledger part caption")[-1].group(0)
print(f"anchors: {N_ROWS} ledger rows (last {LAST_ROW}), total {TOTAL!r}, "
      f"last part caption {LAST_PART!r}")


def poison_total_lowered():
    old = f"{TOTAL} defects"
    if BASE.count(old) < 1:
        raise SystemExit(f"HARNESS FAILURE: main.tex never says {old!r}")
    return BASE.replace(old, f"{word(N_ROWS - 1)} defects", 1)


def poison_last_row_deleted():
    return BASE.replace(LAST_ROW_TEXT, "", 1)


def poison_bibliography_inflated():
    def bump(m):
        v = to_int(m.group(1))
        if v is None:
            raise SystemExit(f"HARNESS FAILURE: {m.group(1)!r} is not a number word")
        return word(v + 1) + m.group(2)
    out, n = re.subn(rf"({NUM})(\s+bibliography entries)", bump, BASE, count=1,
                     flags=re.I)
    if not n:
        raise SystemExit("HARNESS FAILURE: no 'N bibliography entries' claim in main.tex")
    return out


def poison_enumerated_count_wrong():
    def drop(m):
        v = to_int(m.group(1))
        if v is None or v <= 0:
            raise SystemExit(f"HARNESS FAILURE: cannot decrement {m.group(1)!r}")
        return word(v - 1) + m.group(2)
    # re.I: the claim opens a sentence, so it reads "Nineteen of the forty-two---(7)".
    # Without it this anchor matched nothing and the poison silently did not apply -- the
    # exact failure this file was rewritten to make impossible.
    out, n = re.subn(rf"({NUMWORD})(\s+of\s+the\s+{NUM}---\()", drop, BASE,
                     count=1, flags=re.I)
    if not n:
        raise SystemExit("HARNESS FAILURE: no 'N of the M---(...)' enumerated-list claim "
                         "in main.tex")
    return out


def poison_part_range_broken():
    broken = re.sub(r"rows (\d+)--(\d+)",
                    lambda m: f"rows {m.group(1)}--{int(m.group(2)) - 1}", LAST_PART)
    return BASE.replace(LAST_PART, broken, 1)


def run(tag, mutate, expect_fail=True):
    text = mutate()
    if expect_fail and text == BASE:
        # Only for a POISON: the baseline case is deliberately the unpoisoned document, and
        # it is the control that tells "the gate is weak" from "the poison never applied".
        raise SystemExit(f"HARNESS FAILURE: poison {tag!r} produced an UNCHANGED document, "
                         f"so it tests nothing -- this is the failure mode that made three "
                         f"of these anchors dead")
    d = Path(tempfile.mkdtemp(prefix="pc."))
    try:
        (d / "scripts").mkdir()
        shutil.copy(GATE, d / "scripts" / "check_prose_counts.py")
        (d / "main.tex").write_text(text)
        shutil.copy(R / "gen_appendix.tex", d / "gen_appendix.tex")
        shutil.copy(R / "references.bib", d / "references.bib")
        shutil.copytree(R / "records", d / "records")
        (d / "ddl").symlink_to(R.parents[2] / "ddl")
        r = subprocess.run([sys.executable, str(d / "scripts" / "check_prose_counts.py")],
                           capture_output=True, text=True, cwd=str(d))
        err = [l.strip()[2:] for l in r.stderr.splitlines() if l.strip().startswith("-")]
        ok = (r.returncode != 0) == expect_fail
        print(f"{'CAUGHT ' if ok else 'MISSED '}{tag:34s} exit={r.returncode}  "
              f"{(err[0][:104] if err else (r.stdout.splitlines() or [''])[-1][:104])}")
        return ok
    finally:
        shutil.rmtree(d, ignore_errors=True)


CASES = [
    ("baseline_unpoisoned", lambda: BASE, False),
    ("prose_count_lowered_by_one", poison_total_lowered, True),
    ("last_ledger_row_deleted", poison_last_row_deleted, True),
    ("bibliography_count_inflated", poison_bibliography_inflated, True),
    ("enumerated_list_count_wrong", poison_enumerated_count_wrong, True),
    ("caption_part_range_broken", poison_part_range_broken, True),
]

res = [run(tag, mutate, expect) for tag, mutate, expect in CASES]
print(f"\n{sum(res)}/{len(res)} poison tests behaved as designed")
raise SystemExit(0 if all(res) else 1)
