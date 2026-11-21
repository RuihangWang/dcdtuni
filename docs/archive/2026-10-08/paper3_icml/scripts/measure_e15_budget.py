#!/usr/bin/env python3
"""Gate E15's `G16_budget_honoured` disclosure: which arm actually spent the budget it was given.

`records/e15_place.json` carries, per budget and per arm, the set of solver calls the arm really
billed (`spent_set`) and whether that equals the budget it was handed (`equals_budget`).  A round-7
review found that field had NO reader in `scripts/`, so the one place in the manuscript where a
matched-budget comparison is disclosed as not quite matched was prose-carried:

    "The ratio is of budgets \\emph{asked for}: F2 cannot spend one exactly, because $85$-solve
     refreshes do not tile it, and it used $88$ of $128$, $176$ of $256$ and $440$ of $512$, so
     ``S beats F2 at the two smaller budgets'' is partly a granularity artefact and is reported
     as such."

Six numbers and one admission, none of them compared with anything.

WHY THIS IS A SEPARATE FILE AND NOT AN EXTENSION OF `measure_e15.py`.  Both read the same record,
and `measure_e15.py` already pins the neighbourhood size, the degeneracy of arm G, the contrasts
and the F2-over-S budget ratio.  It does not read `G16_budget_honoured` at all.  This file reads
only that field, its granularity, and the sentence that discloses it, so the two gates have
disjoint write sets and neither can be broken by an edit to the other.

WHAT IS CHECKED.

  1. The record is internally consistent: `G16_budget_honoured` agrees with the three `G16:`
     strings the record's own `notes` list carries, and its budgets are exactly
     `rec["budgets"]`.
  2. The disclosure is TRUE: S, R and F1 spend their budget exactly at every budget; F2 does not,
     at any of them; and F2's shortfall is a granularity effect and not a crash -- one refresh
     granularity `g` divides every amount F2 spent, `spent <= budget < spent + g` at every budget,
     and `g` is at least the per-refresh solve count the record states.
  3. The prose is pinned: every number in that sentence must equal the record, the granularity
     artefact must be named as one, and "the two smaller budgets" must be the two budgets at
     which the record says S beats F2 -- not two of three chosen by whoever wrote the sentence.
     ZERO matches is a FAILURE, because an anchored pattern that stops matching is a gate that
     has gone dead and the prose moving is the usual reason.

Usage: measure_e15_budget.py [--check] [--print-values] [--record PATH] [--tex PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from math import gcd
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ARMS = ("S", "R", "F1", "F2")
EXACT_ARMS = ("S", "R", "F1")
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


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


def note_spent(rec):
    """The spent sets the record's own `notes` prose states, parsed back out of it.

    `notes` carries three hand-shaped strings of the form
    `G16: budget 128 spent S=[128] R=[128] F1=[128] F2=[88] (...)`.  They are a second statement
    of the same quantities inside the same file, so they are compared with the structured field
    rather than trusted: a record that disagrees with itself is the N8 defect in miniature.
    """
    out = {}
    for n in rec.get("notes") or []:
        m = re.match(r"G16: budget (\d+) spent S=\[([\d, ]+)\] R=\[([\d, ]+)\] "
                     r"F1=\[([\d, ]+)\] F2=\[([\d, ]+)\]", n)
        if m:
            out[m.group(1)] = {arm: [int(x) for x in m.group(i).split(",") if x.strip()]
                               for i, arm in enumerate(ARMS, start=2)}
    return out


def f2_granularity(g16, budgets, problems):
    """The one refresh granularity F2's spends imply, or None.

    F2 bills in whole refreshes, so every amount it spent must be a multiple of one granularity
    and adding one more refresh must overshoot the budget it was given -- that is what makes the
    shortfall a granularity artefact rather than a crash or an early stop.
    """
    spent = [g16[b]["F2"]["spent_set"] for b in budgets]
    flat = [s for ss in spent for s in ss]
    if not flat or any(len(ss) != 1 for ss in spent):
        problems.append(f"e15-budget: F2's spent_set is {spent}; this gate expects exactly one "
                        f"amount per budget, so either the arm's spend varied across tasks and "
                        f"the single figure in the body is a summary of a distribution, or the "
                        f"record's shape changed and this anchor is dead")
        return None
    g = flat[0]
    for x in flat[1:]:
        g = gcd(g, x)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--print-values", action="store_true",
                    help="print the exact strings the anchors expect, so the prose is written "
                         "FROM the record rather than next to it")
    ap.add_argument("--record", default=str(HERE / "records" / "e15_place.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp = Path(args.record)
    if not rp.exists():
        print("E15 BUDGET-HONOUR GATE FAILED:", file=sys.stderr)
        print(f"  - FAIL: no E15 record at {rp}; the budget-honour disclosure in the body "
              f"would be unchecked. Regenerate with "
              f"ddl/case/benchC/E15_place_search/score_e15.py. An absent record is not a skip: "
              f"'the evidence is not here' and 'the evidence agrees' must not print the same "
              f"thing", file=sys.stderr)
        return 2
    try:
        rec = json.loads(rp.read_text())
    except Exception as e:                                # noqa: BLE001
        print("E15 BUDGET-HONOUR GATE FAILED:", file=sys.stderr)
        print(f"  - FAIL: {rp} does not parse: {e}", file=sys.stderr)
        return 2

    problems: list[str] = []
    g16 = rec.get("G16_budget_honoured")
    if not g16:
        print("E15 BUDGET-HONOUR GATE FAILED:", file=sys.stderr)
        print(f"  - FAIL: {rp.name} carries no G16_budget_honoured field, so nothing records "
              f"which arm actually spent the budget it was given and the body's disclosure is "
              f"unanchored. This is a failure rather than a skip: the field is the only record "
              f"of the unequal-budget confound the body discloses", file=sys.stderr)
        return 1
    budgets = [str(b) for b in rec.get("budgets") or []]
    if sorted(g16) != sorted(budgets):
        problems.append(f"e15-budget: G16_budget_honoured covers budgets {sorted(g16)} while "
                        f"rec['budgets'] is {sorted(budgets)}, so the disclosure is about a "
                        f"different sweep than the one the rest of the record reports")
    budgets = [b for b in budgets if b in g16] or sorted(g16)

    sens = (rec.get("sens_solves_per_refresh") or [None])[0]
    if sens is None:
        problems.append("e15-budget: the record carries no sens_solves_per_refresh, so the "
                        "body's '$85$-solve refreshes do not tile it' has nothing to be pinned "
                        "to and the granularity arithmetic below cannot be checked")

    if args.print_values:
        print(json.dumps({
            "budgets": budgets,
            "spent": {b: {a: g16[b][a]["spent_set"] for a in ARMS if a in g16[b]} for b in budgets},
            "equals_budget": {b: {a: g16[b][a]["equals_budget"] for a in ARMS if a in g16[b]}
                              for b in budgets},
            "sens_solves_per_refresh": sens,
            "f2_granularity": f2_granularity(g16, budgets, []),
        }, indent=1))
        return 0

    # ---- 1. the record agrees with its own prose --------------------------------
    notes = note_spent(rec)
    if not notes:
        problems.append("e15-budget: the record's `notes` list carries no `G16: budget ... spent "
                        "...` line, so the structured field is the only statement of these "
                        "numbers and there is nothing inside the record to reconcile it with")
    n_note_mismatch = 0
    for b in budgets:
        if b not in notes:
            problems.append(f"e15-budget: the record's notes list carries no `G16: budget {b} "
                            f"spent ...` line, so the {b} entry of G16_budget_honoured is a "
                            f"second, unreconciled statement of what F2 spent")
            continue
        for arm in ARMS:
            a_notes = notes[b].get(arm)
            a_field = (g16[b].get(arm) or {}).get("spent_set")
            if a_notes != a_field:
                n_note_mismatch += 1
                problems.append(f"e15-budget: {rp.name} disagrees WITH ITSELF about arm {arm} "
                                f"at budget {b}: G16_budget_honoured says spent_set = "
                                f"{a_field} while its own notes line says {a_notes}")
    if notes and not n_note_mismatch:
        print(f"  e15-budget: G16_budget_honoured agrees with the {len(notes)} `G16:` line(s) in "
              f"the record's own notes list, arm by arm")

    # ---- 2. is the disclosure true? ---------------------------------------------
    for b in budgets:
        for arm in EXACT_ARMS:
            a = g16[b].get(arm)
            if a is None:
                problems.append(f"e15-budget: G16_budget_honoured[{b}] has no arm {arm}, so the "
                                f"record cannot support the body's statement that only F2 fails "
                                f"to spend its budget")
                continue
            if a.get("equals_budget") is not True or a.get("spent_set") != [int(b)]:
                problems.append(f"e15-budget: arm {arm} at budget {b} spent {a.get('spent_set')} "
                                f"with equals_budget = {a.get('equals_budget')!r}. The body's "
                                f"disclosure is that ONLY the current-linearisation arm F2 "
                                f"cannot spend its budget exactly; if {arm} also could not, the "
                                f"sentence understates the confound")
        f2 = g16[b].get("F2") or {}
        if f2.get("equals_budget") is not False:
            problems.append(f"e15-budget: F2 at budget {b} has equals_budget = "
                            f"{f2.get('equals_budget')!r} and spent_set = "
                            f"{f2.get('spent_set')}, so the body's 'F2 cannot spend one exactly' "
                            f"is false at this budget and the granularity caveat it hangs the "
                            f"S-beats-F2 result on does not apply")
    print(f"  e15-budget: {'/'.join(EXACT_ARMS)} spend their budget exactly at all "
          f"{len(budgets)} budget(s); F2 does not at any of them")

    g = f2_granularity(g16, budgets, problems)
    if g:
        if sens is not None and g < sens:
            problems.append(f"e15-budget: F2's implied refresh granularity is {g} solves but the "
                            f"record states {sens} solves per refresh, so F2 is recorded as "
                            f"spending less than one refresh costs and the granularity reading "
                            f"of its shortfall is incoherent")
        for b in budgets:
            spent = (g16[b]["F2"].get("spent_set") or [None])[0]
            if spent is None or not (spent <= int(b) < spent + g):
                problems.append(f"e15-budget: F2 spent {spent} of {b} with a granularity of {g}, "
                                f"which does not satisfy spent <= budget < spent + granularity: "
                                f"the shortfall is not one refresh short, so 'refreshes do not "
                                f"tile it' is not the explanation")
            if spent is not None and spent % g:
                problems.append(f"e15-budget: F2 spent {spent} at budget {b}, which is not a "
                                f"multiple of the {g}-solve granularity the other budgets imply")
        if not problems:
            print(f"  e15-budget: F2's spend is a multiple of one {g}-solve granularity at every "
                  f"budget ({', '.join(str((g16[b]['F2']['spent_set'] or [None])[0]) for b in budgets)}), "
                  f"and one more refresh overshoots each budget, so the shortfall really is "
                  f"granularity"
                  + (f"; the record states {sens} solves per refresh, {g - sens} fewer than the "
                     f"realised granularity, which is reported and not reconciled here"
                     if sens is not None and g != sens else ""))

    # ---- 3. the prose ------------------------------------------------------------
    src = Path(args.tex).read_text()
    n_scored = rec.get("n_scored")
    con = rec.get("contrasts") or {}
    wins_at = [b for b in budgets
               if ((con.get(b) or {}).get("S_vs_F2_relaxed") or {}).get("wins") == n_scored
               and n_scored]
    spent = {b: (g16[b]["F2"].get("spent_set") or [None])[0] for b in budgets}
    specs = [
        (r"it\s+used \$(\d+)\$ of \$(\d+)\$, \$(\d+)\$ of \$(\d+)\$ and \$(\d+)\$ of\s+\$(\d+)\$",
         tuple(x for b in budgets for x in (spent[b], b)),
         "what F2 actually spent against each budget it was given"),
        (r"because \$(\d+)\$-solve refreshes do not tile it", sens,
         "the per-refresh solve count that makes F2's budget unspendable exactly"),
        (r"arm F2 at the\s+(\w+) smaller budgets", WORDS.get(len(wins_at), str(len(wins_at))),
         "how many of the budgets S beats F2 at, stated where the arm is introduced"),
        (r"``S beats F2 at the (\w+) smaller budgets''", WORDS.get(len(wins_at), str(len(wins_at))),
         "how many of the budgets S beats F2 at, stated inside the disclosed caveat"),
    ]
    specs = [s for s in specs if s[1] is not None and
             (not isinstance(s[1], tuple) or all(x is not None for x in s[1]))]
    claim_scoped(src, specs, problems, "e15-budget")

    if sorted(wins_at) != sorted(budgets[:len(wins_at)]):
        problems.append(f"e15-budget: the record says S beats F2 on all {n_scored} tasks at "
                        f"budgets {wins_at}, which are NOT the {len(wins_at)} smallest of "
                        f"{budgets}, so the body's 'the two smaller budgets' picks the wrong ones")
    else:
        print(f"  e15-budget: 'the smaller budgets' are {wins_at}, the {len(wins_at)} smallest "
              f"of {budgets}, at which the record says S beats F2 on all {n_scored} tasks")

    # The caveat must be named as a caveat.  A sentence that quotes the shortfall without
    # saying what it does to the comparison is the disclosure the record exists to force.
    for pat, what in (
            (r"F2 cannot spend one exactly", "that F2 cannot spend a budget exactly"),
            (r"is partly a granularity\s+artefact and is reported as such",
             "that the S-beats-F2 result is partly a granularity artefact, and that it is "
             "reported as one"),
            (r"The ratio is of budgets\s+\\emph\{asked for\}",
             "that the headline budget ratio is a ratio of budgets ASKED FOR rather than spent")):
        if not re.search(pat, src, re.S):
            problems.append(f"e15-budget: main.tex no longer states {what} (pattern {pat!r}). "
                            f"G16_budget_honoured records equals_budget = false for F2 at every "
                            f"budget, so a body that quotes the win without the caveat is "
                            f"quoting an unequal-budget comparison as a matched one")
        else:
            print(f"  e15-budget: the prose carries {what}")

    if rec.get("problems"):
        problems.append(f"e15-budget: score_e15.py recorded {len(rec['problems'])} unresolved "
                        f"problem(s) in the record itself: {rec['problems'][:2]}")

    if problems:
        print("E15 BUDGET-HONOUR GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print(f"e15 G16_budget_honoured agrees with {rp.name} and with the body's disclosure "
          f"(spent sets, granularity, and the caveat that the comparison is not exactly "
          f"matched-budget)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
