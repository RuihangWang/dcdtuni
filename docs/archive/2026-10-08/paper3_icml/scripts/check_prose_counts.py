#!/usr/bin/env python3
"""Every count stated in prose, compared against the thing it counts.

Two count-checks already existed and BOTH were dead, which a review demonstrated rather
than argued:

  * `check_manuscript.py` built its set of stated defect counts from four regexes, none of
    which can match this document.  `Lessons: (\\w+) defects` cannot match
    "Lessons: twenty-eight defects" because `\\w` excludes the hyphen; `(\\w+) of the
    (\\w+)---\\(` cannot match "Thirteen of the twenty-eight---(7)" for the same reason.
    So the set was empty, neither assertion could ever fire, and the docstring above it
    claimed the three counts "all agree".  Its `ledger` variable parsed only the text
    before `\\label{tab:defects}`, i.e. rows 1-11 of a five-part table, so had the set been
    populated the check could only have produced a false failure.
  * `pdf_content_gate.py` searched `all (twenty-[a-z]+|\\d{1,2}) (?:defects|rows)` and
    matched zero strings, because the source reads "list all twenty-eight\\nwith the
    gate" -- the count and the noun are separated by a newline and by other words.  A
    review inserted a lowercase "all twenty-seven defects" into a document rendering
    twenty-eight rows and the gate passed; deleting rows 26-28 from the source produced
    "25 ledger rows (25 found) ... OK" with all nine prose claims of "twenty-eight" intact.

So the round-2 defect -- prose promises N, the artifact contains fewer, the build stays
green -- was still reachable, through the source rather than the renderer.  This is the
sixth instance of the campaign's own pattern, a check whose denominator can be zero
passing on zero, and it was inside the gate written to close that pattern.

Two rules follow, and both are enforced below rather than intended:

  1. Every ground truth is COUNTED from an artifact, never typed here.
  2. For each category, finding ZERO prose claims is a FAILURE, not a pass.  A regex that
     stopped matching is indistinguishable from a document that stopped claiming, and only
     one of those is acceptable.

This file also carried a THIRD dead count-check, and it is deleted rather than kept:
`claims()` and `dedup()`, a 60-character-window scan for a number near a noun, which nothing
called -- the live scan is the anchored `PATTERNS` list in `main()`, because a window that
loose reported 40 of 50 "defect" claims as disagreeing with the ledger and had to be
replaced.  Dead code in a gate file is precisely how the two checks above survived being
dead, so an uncalled function here is treated as a defect and not as documentation.

The one relaxation this gate does carry is DISCLOSED rather than removed: a ``...'' span is
a quotation of what an earlier draft or a dead check said, not a claim by this document, so
quoted spans are blanked before the total-claim scan (blanked, not deleted, so offsets and
line numbers stay right).  The count of exempted spans is printed on every run, because a
relaxation nobody can see is how a gate goes dead in the first place.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _repo_root() -> Path | None:
    """Walk up until the repository root, rather than assuming a depth.

    `HERE.parents[2]` is right in the tree and raises IndexError anywhere else, which made
    this gate crash -- exit 1 with a traceback and no findings -- the first time it was run
    from a shallow sandbox to test whether it fails when poisoned.  A gate that cannot be
    run outside its exact directory cannot be tested, and an untested gate is how the two
    dead count-checks this replaces survived.
    """
    for cand in [HERE, *HERE.parents]:
        if (cand / "ddl" / "docs").is_dir() or (cand / ".git").exists():
            return cand
    return None


REPO = _repo_root()

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
# Explicit rather than `(?:twent|thirt|...)[a-z-]*`: a greedy class containing the hyphen
# swallows the "---" that follows a count in this document ("Thirteen---(7), (8), ..."),
# and only regex backtracking happened to give it back.  Spelling the compounds out makes
# the boundary a real word boundary instead of a lucky one.
# Grouped, and this is not cosmetic: `_U` ungrouped makes `-{_U}` expand to
# `-seventeen|thirteen|fourteen|...`, where the hyphen binds only to the FIRST alternative.
# "twenty-eight" then matched as "twenty" followed by nothing, the `(?![\w-])` boundary saw
# the hyphen and rejected it, and every compound number word in the document became
# invisible -- which is how the two dead count-checks this replaces died in the first place.
_U = "(?:" + "|".join(sorted(UNITS, key=len, reverse=True)) + ")"
_T = "(?:" + "|".join(sorted(TENS, key=len, reverse=True)) + ")"
NUMWORD = rf"(?:{_T}(?:-{_U})?|{_U})"
NUM = rf"(?:{NUMWORD}|\d+)"


def to_int(tok: str) -> int | None:
    t = tok.strip().lower()
    if t.isdigit():
        return int(t)
    t = t.replace(" ", "-")
    if t in UNITS:
        return UNITS[t]
    if t in TENS:
        return TENS[t]
    if "-" in t:
        a, _, b = t.partition("-")
        if a in TENS and b in UNITS:
            return TENS[a] + UNITS[b]
    return None


def flat(src: str) -> str:
    """Whitespace-normalised, so a count and its noun split across a line still match."""
    return re.sub(r"\s+", " ", src)


def enumerated_lists(src: str):
    """`<numword>---(a), (b), (16)--(19)---were` : the count against the items listed.

    Two places in this document state how many of the defects were in the checking
    apparatus and then list them, and a review found them disagreeing (Thirteen including
    (28) in the body, Twelve excluding it in the appendix) about the same predicate.  A
    list whose length is stated in words beside it is checkable, so it is checked.
    """
    f = flat(src)
    out = []
    for m in re.finditer(rf"(?<![\w-])({NUM})(?!\w)(?:\s+of\s+the\s+(?<![\w-])({NUM})(?!\w))?"
                         r"\s*(?:---|--|\\emdash)\s*"
                         r"((?:\(\d+\)\s*(?:--|-|\sto\s)\s*\(\d+\)\s*,?\s*"
                         r"|\(\d+\)\s*,?\s*)+)"
                         r"(?:---|--)", f, re.I):
        stated = to_int(m.group(1))
        # group 3, not 2: the total in "Thirteen of the twenty-eight---" is group 2
        items = re.findall(r"\((\d+)\)(?:\s*(?:--|-)\s*\((\d+)\))?", m.group(3))
        n = 0
        for a, b in items:
            n += (int(b) - int(a) + 1) if b else 1
        total = to_int(m.group(2)) if m.group(2) else None
        out.append({"stated": stated, "listed": n, "total": total,
                    "snippet": m.group(0)[:90], "raw": m.group(1)})
    for m in re.finditer(rf"(?<![\w-])({NUM})(?!\w)\s*,\s*\(?(\d+)\)?\s*"
                         r"(?:--|-|\sto\s)\s*\(?(\d+)\)?\s*,", f, re.I):
        stated = to_int(m.group(1))
        lo, hi = int(m.group(2)), int(m.group(3))
        out.append({"stated": stated, "listed": hi - lo + 1,
                    "snippet": m.group(0)[:90], "raw": m.group(1)})
    return out


# The ledger is ONE section, and `^(\d{1,2}) & ` over the whole file matched any table
# whose first column is a one- or two-digit integer.  A new results table did exactly that
# and this gate reported the ledger as non-contiguous 1..50 AND the prose count of
# "forty-two" as wrong -- two findings, neither of them about the ledger, and findings that
# are not about the thing they name are how a gate gets ignored.  So the scan is scoped, and
# a missing section is a failure rather than an empty row list.
LEDGER_SECTION = re.compile(r"\\section\{The defect ledger\}(.*?)(?=\\section\{|\\Z)",
                            re.S)


def ledger_rows(main_tex: str):
    """Row numbers of the defect ledger, or None if the ledger section cannot be found."""
    m = LEDGER_SECTION.search(main_tex)
    if not m:
        return None
    return sorted({int(n) for n in re.findall(r"^(\d{1,2}) & ", m.group(1), re.M)})


def main() -> int:
    main_tex = (HERE / "main.tex").read_text(encoding="utf-8")
    gen = HERE / "gen_appendix.tex"
    gen_tex = gen.read_text(encoding="utf-8") if gen.exists() else ""
    doc = main_tex + "\n" + gen_tex
    fails, notes = [], []

    # ---- ground truths, all counted -------------------------------------------
    rows = ledger_rows(main_tex)
    if rows is None:
        fails.append("no \\section{The defect ledger} in main.tex, so the ledger's row "
                     "count -- the denominator of the defect-count claims and of the "
                     "part-tiling check -- cannot be measured")
        rows = []
    n_rows = max(rows) if rows else 0
    gaps = sorted(set(range(1, n_rows + 1)) - set(rows))
    if gaps:
        fails.append(f"ledger rows are not contiguous 1..{n_rows}: missing {gaps}")
    if n_rows < 20:
        fails.append(f"only {n_rows} ledger rows parsed from main.tex; a denominator that "
                     f"small means the row regex stopped matching, not that the defects "
                     f"went away")

    bib = HERE / "references.bib"
    n_bib = len(re.findall(r"^@", bib.read_text(encoding="utf-8"), re.M)) if bib.exists() else 0
    if n_bib == 0:
        fails.append("references.bib has no entries, or the entry regex stopped matching")

    parts = re.findall(r"part (\d+) of (\d+): rows (\d+)--(\d+)", flat(main_tex))
    n_parts = len(parts)

    man = HERE / "records" / "measurement_archive.json"
    n_arch = 0
    if man.exists():
        m = json.loads(man.read_text(encoding="utf-8"))
        n_arch = len(m["restored"]) + len(m.get("covered_elsewhere", []))

    plan = (REPO / "ddl" / "docs" / "AI_DESIGN_PLAN.md") if REPO else None
    amd = sorted({int(n) for n in re.findall(r"^## Amendment (\d+)",
                                             plan.read_text(encoding="utf-8"), re.M)}) \
        if plan and plan.exists() else []
    n_amd = max(amd) if amd else 0
    amd_gaps = sorted(set(range(1, n_amd + 1)) - set(amd))
    if amd_gaps:
        fails.append(f"AI_DESIGN_PLAN.md amendments are not contiguous 1..{n_amd}: "
                     f"missing {amd_gaps}")

    # ---- prose claims, each against its ground truth ---------------------------
    # A window around the noun is far too loose: this document says "defect (8)", "ledger
    # row (22)" and "two measurements of one oracle's noise" constantly, and a 60-character
    # window reported 40 of 50 "defect" claims as disagreeing with 28.  So the patterns
    # below encode the phrasings a TOTAL is actually stated in, and each one is required to
    # match at least once -- a pattern that stops matching is the failure this gate exists
    # to catch, and it is reported as such rather than as a clean pass.
    PATTERNS = [
        (rf"(?:all\s+)?(?<![\w-])({NUM})(?!\w)\s+defects", n_rows, "defects",
         "'all twenty-N defects' / 'twenty-N defects'"),
        (rf"(?<![\w-])({NUM})(?!\w)\s+bibliography entries", n_bib, "bibliography entries",
         "'N bibliography entries'"),
        (rf"(?<![\w-])({NUM})(?!\w)\s+measurements present", n_arch, "measurements",
         "'N measurements present' (the archive's own total)"),
    ]
    fd = flat(doc)
    fd = re.sub(r"\\(?:textbf|emph|textit|textsc)\{([^{}]*)\}", r"\1", fd)
    # A quoted span is a citation of what an earlier draft or a dead check said, not a claim
    # by this document -- and quoting withdrawn numbers is this manuscript's whole method.
    # Ledger row (31) has to name the strings the dead regexes missed, which are themselves
    # counts, so the total-claim scan runs on the text with ``...'' blanked.  Blank, not
    # delete, so offsets and line numbers stay right.  The count is printed, because a
    # relaxation nobody can see is how a gate goes dead in the first place.
    n_quoted = len(re.findall(r"``.*?''", fd))
    fd_scan = re.sub(r"``.*?''", lambda m: " " * len(m.group(0)), fd)
    for pat, want, label, why in PATTERNS:
        ms = list(re.finditer(pat, fd_scan, re.I))
        if not ms:
            fails.append(f"no prose matches {why}; either the document stopped stating that "
                         f"total or this pattern stopped matching, and a count-check with "
                         f"nothing to check is the defect it exists to catch")
            continue
        bad = []
        for m in ms:
            v = to_int(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
            if v != want:
                bad.append((v, fd[max(0, m.start() - 40):m.start() + 50]))
        notes.append(f"{label}: {len(ms)} total-claim(s) matching {why}, "
                     f"{len(bad)} disagreeing with the counted {want}")
        for v, ctx in bad:
            fails.append(f"prose says {v} {label} but the artifact counts {want}: "
                         f"...{ctx.strip()}...")

    # "all twenty-N" phrasings where the noun is on the next line, which is exactly the case
    # the dead check in pdf_content_gate.py missed
    for m in re.finditer(rf"\ball\s+(?<![\w-])({NUM})(?!\w)\s+(\w+)", fd_scan, re.I):
        noun = m.group(2).lower()
        want = {"defects": n_rows, "rows": n_rows, "measurements": n_arch,
                "amendments": n_amd}.get(noun)
        if want is None:
            continue
        v = to_int(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
        if v != want:
            fails.append(f"prose promises all {v} {noun} but the artifact counts {want}: "
                         f"...{fd[m.start():m.start() + 60]}...")

    # ---- the defect-table parts must tile the ledger exactly -------------------
    if n_parts:
        covered, totals, order = set(), set(), []
        for a, b, lo, hi in parts:
            totals.add(int(b))
            order.append((int(a), int(lo), int(hi)))
            covered |= set(range(int(lo), int(hi) + 1))
        if [x[0] for x in order] != list(range(1, n_parts + 1)):
            fails.append(f"the ledger's {n_parts} parts are not numbered 1..{n_parts} in "
                         f"order: {[x[0] for x in order]}")
        if len(totals) != 1 or totals.pop() != n_parts:
            fails.append(f"{n_parts} defect-table parts found but their 'part a of b' "
                         f"captions disagree about b")
        if covered != set(range(1, n_rows + 1)):
            fails.append(f"the defect-table parts cover rows {sorted(covered)[:3]}.."
                         f"{sorted(covered)[-3:] if covered else []} but the ledger has "
                         f"1..{n_rows}; untiled: "
                         f"{sorted(set(range(1, n_rows + 1)) - covered)}")
        else:
            notes.append(f"{n_parts} ledger parts tile rows 1..{n_rows} exactly")
    else:
        fails.append("no 'part a of b: rows lo--hi' caption found; the ledger table is no "
                     "longer split, or the caption regex stopped matching")

    # ---- enumerated lists must contain as many items as they state -------------
    lists = enumerated_lists(doc)
    if not lists:
        fails.append("no '<count>---(a), (b), ...---' list found; the document has two and "
                     "a review found them disagreeing, so this regex has stopped matching")
    for L in lists:
        if L.get("total") is not None and L["total"] != n_rows:
            fails.append(f"an enumerated list says 'of the {L['total']}' but the ledger has "
                         f"{n_rows} rows: ...{L['snippet']}...")
        if L["stated"] != L["listed"]:
            fails.append(f"a list states {L['raw']} ({L['stated']}) items and contains "
                         f"{L['listed']}: ...{L['snippet']}...")
        else:
            notes.append(f"enumerated list states {L['stated']} and lists {L['listed']}")

    # ---- the amendments the manuscript says it has ----------------------------
    for m in re.finditer(rf"Amendments?\s+1\s*(?:--|-|to)\s*(\d+)", flat(doc)):
        v = int(m.group(1))
        if v != n_amd:
            fails.append(f"prose cites Amendments 1--{v} but AI_DESIGN_PLAN.md has "
                         f"{n_amd}")
        else:
            notes.append(f"'Amendments 1--{v}' agrees with the plan's {n_amd}")

    for n in notes:
        print("note:", n)
    print(f"PROSE COUNT GATE: ledger {n_rows} rows in {n_parts} parts, bibliography "
          f"{n_bib} entries, archive {n_arch} measurements, plan {n_amd} amendments; "
          f"{len(lists)} enumerated lists checked, {n_quoted} quoted spans exempted from "
          f"the total-claim scan")
    if fails:
        print("PROSE COUNT GATE FAILED:", file=sys.stderr)
        for f in fails:
            print("  -", f, file=sys.stderr)
        return 1
    print("PROSE COUNT GATE OK (every stated count agrees with the thing it counts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
