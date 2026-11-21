#!/usr/bin/env python3
"""Gate every E15 (placement-block) number the manuscript states against its record.

Reads `records/e15_place.json`, written by `ddl/case/benchC/E15_place_search/score_e15.py`
from the per-task records of `run.py`.  Nothing here is prose-carried: each pattern below is
anchored to the sentence that states the number, every match must equal the record, and ZERO
matches is a failure, because an anchored pattern that stops matching is a gate that has gone
dead and the prose moving is the usual reason.

This gate exists because the first draft of the E15 paragraph carried two numbers that were
wrong, and both were found by writing this file rather than by reading the paragraph: a
"maximum relative difference of 2.0e-14" that was actually the median margin expressed in
PERCENT (the relative difference is 1.9e-15), and "R a median of 192" that was a median over
only the two tasks on which R reaches the optimum at all.  A percentage and a ratio are one
character apart in LaTeX and neither is visible to a reader.

Usage: measure_e15.py [--check] [--record PATH] [--tex PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def claim_scoped(src, specs, problems, where):
    """Every match of each anchored pattern must equal the value the record gives."""
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for "
                            f"{label}, so that number is unchecked -- the prose moved or the "
                            "anchor is wrong, and a check with nothing to check is not a pass")
            continue
        if isinstance(expected, tuple):
            want = tuple(str(e) for e in expected)
            bad = [h for h in hits if tuple(h) != want]
            shown = " / ".join(want)
        else:
            ok = ({str(expected)} if isinstance(expected, (str, int, float))
                  else {str(e) for e in expected})
            flat = [h if isinstance(h, str) else " / ".join(h) for h in hits]
            bad = [h for h in flat if h not in ok]
            shown = ", ".join(sorted(ok)[:4])
        if bad:
            problems.append(f"{where}: {len(bad)} of {len(hits)} statements of {label} "
                            f"disagree with the record: {sorted(set(map(str, bad)))[:4]} "
                            f"against {shown}")
        else:
            print(f"  {where}: {label} -- all {len(hits)} statement(s) agree with the record")



BUDGET_RATIO_EXEMPT = ()


def sentences(src):
    """(start_offset, text) for each sentence-ish run, split on blank-line-free
    period-space boundaries.  Deliberately crude: the point is to keep a number and
    the words that say what it measures in one window, and a window that is a little
    too wide only risks a false positive, which is then named and exempted by hand
    rather than silently missed."""
    out = []
    start = 0
    for m in re.finditer(r"(?<=\.)\s+(?=[A-Z$\\])|\n\s*\n", src):
        out.append((start, src[start:m.start() + 1]))
        start = m.end()
    out.append((start, src[start:]))
    return out


def budget_ratio_sites(src):
    """Every `$N\times$` sitting in a sentence that is about a budget.

    Returns (line_number, sentence, value).  Line numbers are 1-based and computed
    from the match offset so a finding can be acted on without re-searching.
    """
    found = []
    for start, text in sentences(src):
        if "budget" not in text.lower():
            continue
        for m in re.finditer(r"\$(\d+)\\times\$", text):
            value = int(m.group(1))
            if value in BUDGET_RATIO_EXEMPT:
                continue
            line = src.count("\n", 0, start + m.start()) + 1
            found.append((line, " ".join(text.split()), value))
    return found


def words(n):
    return {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
            7: "seven", 8: "eight"}.get(n, str(n))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report-ratio-sites", action="store_true",
                    help="print every `$N\\times$` site the budget-ratio sweep sees, "
                         "so a window that is too wide or too narrow is visible")
    ap.add_argument("--record", default=str(HERE / "records" / "e15_place.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp, tp = Path(args.record), Path(args.tex)
    if not rp.exists():
        # A check whose denominator can be zero must fail on zero: an absent record is not a
        # skip, because then the four E15 numbers in the body would be unchecked and the suite
        # would still be green on a clean clone.
        print(f"FAIL: no E15 record at {rp}; the placement numbers in the body would be "
              "unchecked. Regenerate with ddl/case/benchC/E15_place_search/score_e15.py.",
              file=sys.stderr)
        return 2
    rec = json.loads(rp.read_text())
    src = tp.read_text()
    problems = []

    prov = rec.get("provenance") or {}
    if "E15" not in str(prov.get("case", "")):
        problems.append(f"e15: the record's provenance names {prov.get('case')!r}, not E15, so "
                        "the manuscript would be quoting a record of unknown origin")
    if rec.get("problems"):
        problems.append(f"e15: score_e15.py recorded {len(rec['problems'])} unresolved "
                        f"problem(s) in the record itself: {rec['problems'][:2]}")

    g18 = rec["G18_armG_degeneracy"]
    g17 = rec["G17_rho_heur_vs_adjoint"]
    p11 = rec["P11_budget_to_optimum"]
    price = p11["price_of_the_adjoint"]
    con = rec["contrasts"]
    budgets = [str(b) for b in rec["budgets"]]
    n_scored = rec["n_scored"]
    n_disk = rec["n_tasks_on_disk"]

    # --- the degeneracy that withdrew arm G -----------------------------------
    if not g18["armG_degenerate_on_all"]:
        problems.append("e15: arm G's length score is not degenerate on every task, so the "
                        "stated reason for withdrawing it does not hold")
    if not g18["L_independent_of_rack_index_on_all"]:
        problems.append("e15: L[i,k,s] depends on the rack index somewhere, so the "
                        "permutation-invariance argument does not hold universally")
    if g18["n_tasks_with_geometry"] != n_disk:
        problems.append(f"e15: only {g18['n_tasks_with_geometry']} of {n_disk} tasks carry the "
                        "geometry measurement; a refused task must still carry it, because the "
                        "degeneracy is a property of the room and not of the base design")

    # --- the contrasts must be what the prose says they are --------------------
    for b in budgets:
        c = con[b]["S_vs_F1_relaxed"]
        if c["wins"] or c["losses"] or c["ties"] != n_scored:
            problems.append(f"e15: at budget {b} S_vs_F1 is {c['wins']}W/{c['ties']}T/"
                            f"{c['losses']}L, not the {n_scored}-of-{n_scored} tie the body states")
        if not c["machine_precision_tie"]:
            problems.append(f"e15: at budget {b} the S/F1 tie is not machine precision "
                            f"(max relative difference {c['max_relative_difference']}), so "
                            "'ties' overstates the agreement")
        r = con[b]["S_vs_R_relaxed"]
        if r["wins"] != n_scored:
            problems.append(f"e15: at budget {b} S beats R on {r['wins']} of {n_scored}, "
                            "not all of them")
    for b in budgets:
        f2 = con[b]["S_vs_F2_relaxed"]
        expect_win = b in ("128", "256")
        if expect_win and f2["wins"] != n_scored:
            problems.append(f"e15: at budget {b} S beats F2 on {f2['wins']} of {n_scored}")
        if not expect_win and f2["ties"] != n_scored:
            problems.append(f"e15: at budget {b} S does not tie F2 on all {n_scored}")

    maxrel = max(con[b][k]["max_relative_difference"]
                 for b in budgets for k in ("S_vs_F1_relaxed", "S_vs_F1_hard")
                 if con[b][k].get("max_relative_difference") is not None)

    r_reach = [s for s, v in rec["budget_to_optimum_per_task"].items() if v["R"] is not None]
    r_never = [s for s, v in rec["budget_to_optimum_per_task"].items() if v["R"] is None]

    specs = [
        (r"ranking all \$(\d+)\$ swaps", rec["neighbourhood_size"][0],
         "the size of the swap neighbourhood"),
        (r"gives a score of relative range\s*\$([\d.]+)\\times10\^\{-16\}\$",
         f"{g18['armG_rel_range_max'] * 1e16:.1f}", "arm G's relative length-score range"),
        (r"zero spread over the rack\s*index on all (\w+) tasks", words(n_disk),
         "the number of tasks on which the rack axis of L is degenerate"),
        (r"\$1 \+ 2NK = (\d+)\$ central differences", price["solves_per_state_finite_difference"],
         "the finite-difference cost of one state's sensitivities"),
        (r"Over budgets \$\\{128, 256, 512\\}\$ on the\s*(\w+) tasks that solve", words(n_scored),
         "the number of scored tasks"),
        (r"maximum relative difference \$([\d.]+)\\times10\^\{-15\}\$",
         f"{maxrel * 1e15:.1f}", "the largest S-versus-F1 relative difference"),
        (r"beats \\textbf\{R\} \(uniform\) \$(\d+)\$ of \$(\d+)\$,\s*\$p = ([\d.]+)\$",
         (n_scored, n_scored, f"{con['128']['S_vs_R_relaxed']['p']:.4f}"),
         "the S-versus-R win count and its exact sign-test p"),
        (r"S \$128\$, F1 \$128\$, F2 \$512\$, and R on only \$(\d+)\$ of the \$(\d+)\s*\$?"
         r"\s*tasks at all, at \$128\$ and \$256\$, never on the other \$(\d+)\$",
         (len(r_reach), n_scored, len(r_never)),
         "the budget-to-optimum counts, including how few tasks R ever reaches"),
        (r"costs (\w+) times the budget", "four",
         "the F2-over-S budget ratio, asserted in words"),
        (r"median\s*per-state \$\\rho\$ of only \$([\d.]+)\$", f"{g17['F1']['median']:.3f}",
         "F1's median per-state agreement with the adjoint"),
        (r"\$([\d.]+)\\%\$ of its states reach \$0\.99\$, against \$(\d+)\\%\$ for F2\s*"
         r"at \$([\d.]+)\$",
         (f"{g17['F1']['frac_at_least_0p99'] * 100:.1f}",
          f"{g17['F2']['frac_at_least_0p99'] * 100:.0f}",
          f"{g17['F2']['median']:.5f}"),
         "the fraction of states reaching rho 0.99 for F1 and F2, and F2's median"),
        (r"It is a diagnostic over\s*\\emph\{(\w+)\} blocks", "three",
         "how many decision blocks the E13/E14/E15 diagnostic now covers"),
    ]
    if price["budget_ratio_F2_over_S"] != 4.0:
        problems.append(f"e15: the F2-over-S budget ratio is {price['budget_ratio_F2_over_S']}, "
                        "so the body's 'four times' is wrong")

    claim_scoped(src, specs, problems, "e15")

    # Every site of the ratio, not only the word-form one the spec above anchors.
    ratio = int(round(price["budget_ratio_F2_over_S"]))
    sites = budget_ratio_sites(src)
    if args.report_ratio_sites:
        for line, sent, value in sites:
            print(f"  ratio site: line {line}  ${value}\\times$  in: {sent[:110]}")
    if not sites:
        problems.append("e15: no `$N\\times$` budget-ratio site was found anywhere in "
                        "main.tex, so the ratio this gate exists to pin is unquoted and the "
                        "check has nothing to check -- either the prose moved or the window "
                        "is wrong, and a check with nothing to check is not a pass")
    bad = [(line, value, sent) for line, sent, value in sites if value != ratio]
    for line, value, sent in bad:
        problems.append(f"e15: main.tex line {line} states a budget ratio of {value}x where "
                        f"records/e15_place.json says {ratio}x: {sent[:100]!r}")
    if sites and not bad:
        print(f"  e15: budget ratio pinned at {ratio}x on all {len(sites)} site(s) "
              f"(lines {sorted({l for l, _, _ in sites})})")

    # The withdrawal of arm G must be visible where the placement opponent is introduced.
    if "exactly degenerate" not in src:
        problems.append("e15: the body no longer says arm G's prior is exactly degenerate, so a "
                        "reader cannot tell why the cheapest opponent is absent")

    if problems:
        print("E15 GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print(f"e15 numbers agree with {rp.name} (direction, ties and denominators included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
