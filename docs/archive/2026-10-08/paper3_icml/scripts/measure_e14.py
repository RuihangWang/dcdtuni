#!/usr/bin/env python3
"""E14's published numbers: checked against the record, not trusted.

Same discipline as `measure_e13.py`: the only tables and figures in this manuscript whose
values are typeset rather than emitted by `fill_results.py` get a script that reads the
record and compares.  This campaign has found five numbers quoted from prose that were
wrong every time anyone looked, and one passage whose DIRECTION was reversed by a
correction and then inherited by a review round, so a number carried across by hand is
treated as unverified until something compares it.

Checked: the win counts, their exact one-sided sign-test p-values and the hard-network
counts for all three pairwise contrasts on both decision blocks; that all three arms billed
equally on every task (G3/G5); that the hard verdict is reported at all (G4); that the one
task which no longer solves is named in the manuscript rather than silently absent from a
denominator; and the DIRECTION of both the diameter win and the assignment loss, because a
previous revision reversed a direction and the next review round inherited it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_ddl_withdrawn import WITHDRAWN, scan_source   # noqa: E402
# What main.tex says, as (wins, n).  Seven tasks, not eight: seed 910034's base design
# does not converge at this revision and is refused under gate G7 rather than dropped
# silently, so every contrast is scored on the seven that solve.  `S_vs_C` is the
# gradient-free constant rule of Amendment 24.3 -- the comparison that decides whether the
# adjoint earns its keep on the block where its ranking is exact.
EXPECT = {
    # Amendment 26.  On DIAMETER the paper's headline is not a win for the adjoint:
    # S_vs_D is 0 of 7 because the hard-network objectives are bitwise identical and
    # ties are losses for the arm claiming superiority under this campaign's own
    # convention.  D_vs_C and D_vs_R are pinned at 7 of 7 because the one-solve constant
    # beats both searched arms on the relaxed objective too.
    "diam":   {"S_vs_R": {"relaxed": (7, 7), "hard": (7, 7)},
               "S_vs_C": {"relaxed": (7, 7), "hard": (7, 7)},
               "C_vs_R": {"relaxed": (7, 7), "hard": (7, 7)},
               "S_vs_D": {"relaxed": (0, 7), "hard": (0, 7)},
               "D_vs_C": {"relaxed": (7, 7), "hard": (7, 7)},
               "D_vs_R": {"relaxed": (7, 7), "hard": (7, 7)}},
    # On ASSIGNMENT D is a different thing entirely: every rack on one CDU is not a legal
    # design, the hard network marks it infeasible on all eight tasks -- which drops the
    # hard denominator to five -- and it loses to both other arms by four orders of
    # magnitude.  Pinned at zero on purpose.  These two contrasts are the control that
    # stops arm D reading as "a better arm": it is one design that happens to be the
    # optimum of one block, and on the other block it is a disaster.
    "assign": {"S_vs_R": {"relaxed": (1, 7), "hard": (1, 6)},
               "S_vs_C": {"relaxed": (2, 7), "hard": (3, 7)},
               "C_vs_R": {"relaxed": (2, 7), "hard": (1, 6)},
               "S_vs_D": {"relaxed": (5, 7), "hard": (5, 5)},
               "D_vs_C": {"relaxed": (0, 7), "hard": (0, 5)},
               "D_vs_R": {"relaxed": (0, 7), "hard": (0, 5)}},
}


# WITHDRAWN now lives in check_ddl_withdrawn.py, which this module imports: one list, two
# call sites.  It used to be defined here and scanned ONLY main.tex, while the ledger rows
# that withdraw each phrase cite scripts under ddl/case/ as the evidence for the
# correction -- so all six survived, asserted as fact, in the very files a reader is pointed
# at.  `check_withdrawn_phrases` below now scans both.


def check_withdrawn_phrases(src):
    """Phrases this round withdrew must not reappear outside a quoted withdrawal.

    The manuscript marks a withdrawn claim by quoting it, so ``...'' spans are exempt and
    everything else is not.  That exemption is narrower than the one a review found in
    check_prose_counts.py, where quoting a false COUNT hid it from the total-claim scan:
    these six phrases are specific enough that naming what was removed is their only
    legitimate use.

    The scan covers main.tex AND `ddl/case/benchC/*.py`.  The second half is the one that
    was missing: rows (30), (35), (36) and (41) each cite a benchC script as the
    correction, and every one of those scripts still asserted the phrase in its own
    docstring.  `scripts/check_ddl_withdrawn.py` scans all of `ddl/case` with the same
    list; this narrower scan stays here so that E14's own gate cannot pass while the code
    it is about contradicts the manuscript.
    """
    problems, notes = [], []
    scan_source("main.tex", src, problems, notes)
    targets = sorted((REPO / "ddl" / "case" / "benchC").glob("*.py"))
    if not targets:
        problems.append("no .py files under ddl/case/benchC to scan, so the half of this "
                        "check that covers the cited scripts had nothing to check")
    for p in targets:
        scan_source(str(p.relative_to(REPO)),
                    p.read_text(encoding="utf-8", errors="replace"), problems, notes)
    if not problems:
        print(f"  withdrawn phrases: {len(WITHDRAWN)} checked over main.tex and "
              f"{len(targets)} benchC script(s), none asserted outside a quote "
              f"({len(notes)} quoted/negated occurrence(s) exempted)")
    return problems


def check_closure_floor(d, src):
    """Row 33, recomputed from two tracked records rather than trusted.

    Asserts all three halves of the claim: the hard-network objectives are bitwise
    identical on every scored task; the designs differ only on air-cooled racks, which
    have no liquid branch; and every relaxed gap is strictly below the campaign's own
    median closure floor.  If any of the three moves, the sentence in section 3 is wrong
    and this fails.
    """
    problems = []
    floor_path = HERE / "records" / "closure_floor.json"
    if not floor_path.exists():
        return [f"row 33 compares against {floor_path.name}, which is not present"]
    floor = json.loads(floor_path.read_text())["closure_rel_gap_hard_feasible"]
    med_floor, n_floor = floor["median"], floor["n"]
    gaps, hard_equal, odd_only = [], 0, True
    for t in d["tasks"]:
        b = t["blocks"]["diam"]
        s_arm, d_arm = b["S"], b.get("D")
        if not d_arm or s_arm["min_J_liq"] is None or d_arm["min_J_liq"] is None:
            continue
        gaps.append((s_arm["min_J_liq"] - d_arm["min_J_liq"]) / d_arm["min_J_liq"])
        if s_arm["hard"]["J"] == d_arm["hard"]["J"]:
            hard_equal += 1
        for c in (b.get("_D_diagnostic_G10") or {}).get("coords", []):
            if c["coord"] % 2 == 0:
                odd_only = False
    if not gaps:
        return ["row 33: no task carries both an S and a D diameter result"]
    if hard_equal != len(gaps):
        problems.append(f"row 33 says the hard-network objectives are bitwise identical on "
                        f"every scored task; they are identical on {hard_equal} of {len(gaps)}")
    if not odd_only:
        problems.append("row 33 says the two designs differ only on air-cooled racks; at "
                        "least one differing coordinate has an even index")
    worst = max(gaps)
    if worst >= med_floor:
        problems.append(f"row 33 says every relaxed S-versus-D gap is below the closure "
                        f"floor; the worst is {worst:.3e} against a median floor of "
                        f"{med_floor:.3e} over n={n_floor}")
    if "1.6\\times10^{-3}" not in src:
        problems.append("main.tex does not state the closure floor it compares the gap with")
    print(f"  closure floor: worst relaxed S-vs-D gap {worst:.3e} < median floor "
          f"{med_floor:.3e} (n={n_floor}); hard objectives bitwise equal on "
          f"{hard_equal}/{len(gaps)}; differing coordinates all air-cooled: {odd_only}")
    return problems


def claim_scoped(src, specs, problems, where):
    """Every match of each anchored pattern must equal the value the record gives.

    specs: (pattern, expected, label).  `expected` may be a single string or a set of
    acceptable strings.  Zero matches is a failure: an anchored pattern that stops matching
    is a gate that has gone dead, and the prose moving is the usual reason.
    """
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for "
                            f"{label} ({pattern!r}), so that number is unchecked -- the "
                            "prose moved or the anchor is wrong, and a check with nothing "
                            "to check is not a pass")
            continue
        if isinstance(expected, tuple):
            want = tuple(str(e) for e in expected)
            bad = [h for h in hits if tuple(h) != want]
            shown = f"{want[0]} of {want[1]}"
        else:
            ok = ({str(expected)} if isinstance(expected, (str, int, float))
                  else {str(e) for e in expected})
            flat = [h if isinstance(h, str) else " of ".join(h) for h in hits]
            bad = [h for h in flat if h not in ok]
            shown = ", ".join(sorted(ok)[:4])
        if bad:
            problems.append(f"{where}: {len(bad)} of {len(hits)} statements of {label} "
                            f"disagree with the record: {sorted(set(map(str, bad)))[:4]} "
                            f"against {shown}")
        else:
            print(f"  {where}: {label} -- all {len(hits)} statement(s) agree with the record")


def check_claim_scoped(d, src):
    """The load-bearing E14 numbers, checked at the sentence that states each one.

    Every expected value is read from the record in this function; nothing is transcribed
    from the manuscript, which is the difference between a gate and a mirror.
    """
    problems = []
    pw = d["scored"]["diam"]["pairwise"]
    sc, sr, sd = pw["S_vs_C"], pw["S_vs_R"], pw["S_vs_D"]
    dc, dr = pw["D_vs_C"], pw["D_vs_R"]
    asg = d["scored"]["assign"]["pairwise"]

    def pct(x):
        return f"{x:.2f}".replace("-", "-")

    specs = [
        # the sentence in section 3 that states the S-over-C margin
        (r"S beats C .{0,90}?median margin \$(-?[\d.]+)\\%\$", pct(sc["rel_margin_pct_median"]),
         "the S-over-C median margin on diameter"),
        # every "median margin $X\%$" anywhere: each must be a margin this record contains
        (r"median margin \$(-?[\d.]+)\\%\$",
         {pct(c["rel_margin_pct_median"])
          for b in ("diam", "assign") for c in d["scored"][b]["pairwise"].values()
          if c.get("rel_margin_pct_median") is not None},
         "any median margin stated in the document"),
        # the S-versus-D counts, anchored in the sentence that makes the claim
        (r"S scores \$(\d+)\$ of \$(\d+)\$", (str(sd["wins_S"]), str(sd["n"])),
         "the S-versus-D relaxed count"),
        (r"bitwise identical\} on\s*\$(\d+)\$ of \$(\d+)\$ tasks",
         (str(sd["hard_n"]), str(sd["hard_n"])),
         "the hard-network identity count"),
        # the win counts stated with \textbf, each anchored to the arm it is about
        (r"S beats C .{0,20}?\\textbf\{\$(\d+)\$ of \$(\d+)\$\}",
         (str(sc["wins_S"]), str(sc["n"])), "the S-beats-C diameter count"),
        (r"S beats R .{0,20}?\\textbf\{\$(\d+)\$ of \$(\d+)\$\}",
         (str(asg["S_vs_R"]["wins_S"]), str(asg["S_vs_R"]["n"])),
         "the S-beats-R assignment count"),
    ]
    claim_scoped(src, specs, problems, "e14")
    return problems


def check_arm_d(d, src):
    """G10, G11 and G9, recomputed from the per-coordinate records.

    Returns a list of problems.  Every number the manuscript states about WHY the exact
    adjoint loses is recomputed here rather than trusted, because the two available
    explanations -- a blind gradient and an exhausted step budget -- imply different
    papers and only the record says which is true.
    """
    problems = []
    pw = d["scored"]["diam"]["pairwise"]
    for name in ("S_vs_D", "D_vs_C", "D_vs_R"):
        if name not in pw:
            problems.append(f"diam: the record has no {name} contrast, so it predates "
                            "Amendment 26 and cannot check this manuscript")
            return problems
        if pw[name].get("matched_budget") is not False:
            problems.append(f"diam {name}: not labelled unequal-budget; D bills one solve "
                            "against the search arms' 24 and the contrast must say so")

    # G10: the mechanism, recomputed coordinate by coordinate.
    diffs, at_target, blind, ratios, never_billed = [], [], 0, [], 0
    n_tasks = 0
    for t in d["tasks"]:
        g = t["blocks"]["diam"].get("_D_diagnostic_G10") or {}
        if not g.get("available"):
            continue
        n_tasks += 1
        diffs.append(g["n_differing_coords"])
        blind += g["n_pred_exactly_zero"]
        never_billed += g["n_correcting_move_never_billed"]
        fd = t["blocks"]["diam"]["S"]["final_diam"]
        at_target.append(sum(1 for x in fd if x == 0))
        for c in g.get("coords", []):
            if c.get("pred_dJ") and c.get("measured_dJ") is not None:
                ratios.append(c["measured_dJ"] / c["pred_dJ"])
    if not n_tasks:
        problems.append("no task carries a G10 diagnostic, so the mechanism the manuscript "
                        "states is unmeasured")
        return problems
    if len(set(diffs)) != 1:
        problems.append(f"the manuscript says S differs from D on the same number of "
                        f"coordinates on every task; the record says {sorted(set(diffs))}")
    if len(set(at_target)) != 1:
        problems.append(f"the manuscript says S reaches the target index on the same number "
                        f"of racks on every task; the record says {sorted(set(at_target))}")
    ratios.sort()
    med = ratios[len(ratios) // 2] if len(ratios) % 2 else \
        0.5 * (ratios[len(ratios) // 2 - 1] + ratios[len(ratios) // 2])
    want = {
        f"{diffs[0]}": "coordinates where S's final diameter vector differs from D's",
        f"{at_target[0]}": "racks at the target index in S's final design",
        f"{len(ratios)}": "coordinate measurements behind the magnitude ratio",
        f"{med:.2f}": "the median first-order magnitude ratio",
        f"{ratios[0]:.2f}": "the low end of that ratio's range",
        f"{ratios[-1]:.2f}": "the high end of that ratio's range",
    }
    for needle, why in want.items():
        if needle not in src:
            problems.append(f"main.tex does not contain {needle!r} ({why})")
    if blind:
        problems.append(f"the manuscript says the adjoint is never exactly zero on the "
                        f"coordinates that separate S from D; the record has {blind} such")

    # G11: the benchmark's own reference field.
    g11 = d["gates"].get("G11_D_equals_dataset_reference_field") or {}
    hits = sum(1 for k, v in g11.items() if k.endswith(":diam") and v is True)
    tot = sum(1 for k in g11 if k.endswith(":diam"))
    if not tot:
        problems.append("G11 recorded nothing, so the manuscript cannot say the optimum is "
                        "in the data file")
    elif f"{hits}$ of the ${tot}" not in src and f"{hits} of {tot}" not in src:
        problems.append(f"main.tex does not state G11's {hits} of {tot}")

    # G8/G9: the withdrawal and the language.
    if not d["gates"].get("G8_narrowing_owed_in_this_commit"):
        problems.append("the record does not owe a narrowing, so this manuscript's "
                        "withdrawal of the diameter search claim is unexplained")
    for bad in ("matched cost", "matched budget"):
        i = 0
        while True:
            i = src.find(bad, i)
            if i < 0:
                break
            window = src[max(0, i - 400):i + 400]
            if "S_vs_D" in window or ("arm D" in window or " D " in window
                                      and "one evaluation" in window):
                problems.append(f"G9: {bad!r} appears within four hundred characters of an "
                                "S-versus-D statement; D's budget is unequal by design")
            i += len(bad)
    print(f"  arm D  diam S_vs_D {pw['S_vs_D']['wins_S']}/{pw['S_vs_D']['n']} relaxed, "
          f"{pw['S_vs_D']['hard_wins_S']}/{pw['S_vs_D']['hard_n']} hard; D_vs_C "
          f"{pw['D_vs_C']['wins_S']}/{pw['D_vs_C']['n']} "
          f"({pw['D_vs_C']['rel_margin_pct_median']:+.2f}%); G10 over {n_tasks} tasks: "
          f"{diffs[0]} differing coords, {at_target[0]} at target, {blind} exactly-zero "
          f"predictions, magnitude ratio {med:.3f}; G11 {hits}/{tot}")
    return problems


def p1(w, n):
    return float(f"{sum(comb(n, i) for i in range(w, n + 1)) / 2 ** n:.6g}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default=None)
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rec = Path(args.record) if args.record else None
    if rec is None:
        # The TRACKED copy first.  Reading the live gitignored loggers by default meant
        # the headline was checked against whichever run happened to be newest, and a
        # reviewer who cloned the repository could not check it at all.
        tracked = HERE / "records" / "e14_search.json"
        if tracked.exists():
            rec = tracked
        else:
            c = sorted(REPO.glob("ddl/case/benchC/E14_fullrank_search/loggers/*/"
                                 "results_e14.json"))
            if not c:
                print("no results_e14.json found, and no tracked copy at "
                      f"{tracked}", file=sys.stderr)
                return 2
            rec = c[-1]
    d = json.loads(rec.read_text())
    sc = d["scored"]
    problems = []

    for block, contrasts in EXPECT.items():
        pw = sc[block].get("pairwise")
        if not pw:
            problems.append(f"{block}: the record has no `pairwise` contrasts, so it is a "
                            f"two-arm record and cannot check the three-arm manuscript")
            continue
        for name, want in contrasts.items():
            if name not in pw:
                problems.append(f"{block}: the record has no {name} contrast")
                continue
            b = pw[name]
            got_rel = (b["wins_S"], b["n"])
            got_hard = (b["hard_wins_S"], b["hard_n"])
            if got_rel != want["relaxed"]:
                problems.append(f"{block} {name}: manuscript says "
                                f"{want['relaxed'][0]} of {want['relaxed'][1]}, record says "
                                f"{got_rel[0]} of {got_rel[1]}")
            if got_hard != want["hard"]:
                problems.append(f"{block} {name} hard: manuscript says "
                                f"{want['hard'][0]} of {want['hard'][1]}, record says "
                                f"{got_hard[0]} of {got_hard[1]}")
            if b["budget_mismatches"]:
                problems.append(f"{block} {name}: arms billed unequal budgets on "
                                f"{len(b['budget_mismatches'])} tasks")
            pr = p1(*got_rel)
            ph = p1(*got_hard) if got_hard[1] else None
            print(f"  {block:6s} {name:7s} relaxed {got_rel[0]}/{got_rel[1]} p={pr}   "
                  f"hard {got_hard[0]}/{got_hard[1]} p={ph}   "
                  f"median margin {b['rel_margin_pct_median']:+.2f}%")


    # Whitespace-normalised, because the source is hard-wrapped: "\textbf{$1$ of\n$8$}"
    # does not contain the literal "1$ of $8$" even though the rendered page says
    # "1 of 8".  The pdf-content gate learned this the same way.
    import re as _re
    src = _re.sub(r"\s+", " ", Path(args.tex).read_text(encoding="utf-8"))
    # The refused task must be named in the manuscript, not just absent from a denominator.
    refused = d.get("tasks_refused_no_solve", [])
    if len(refused) != 1 or refused[0]["seed"] != 910034:
        problems.append(f"expected exactly seed 910034 refused, record says {refused}")
    if "910034" not in src:
        problems.append("main.tex does not name seed 910034, so a reader cannot tell that "
                        "the statistics are over seven of the eight tasks")
    for needle, why in [
        ("7$ of $7$", "the diameter win count"),
        ("1$ of $7$", "the assignment win count"),
        ("0.0078", "the diameter p-value"),
        ("0.992", "the assignment p-value"),
        ("5.07", "the S-over-C median margin on diameter"),
        ("11.42", "the C-over-R median margin on diameter"),
        ("18.64", "the S-over-R median margin on diameter"),
        ("910036", "the task the hard network drops on assignment"),
        ("2$ of $7$", "the S-vs-C and C-vs-R counts on assignment"),
    ]:
        if needle not in src:
            problems.append(f"main.tex does not contain {needle!r} ({why})")
    # The direction must not be reversed, on BOTH contrasts: a previous revision reversed
    # the GP-BO one and the next review round inherited the error (ledger row 28).  So the
    # text has to say the screen beats the constant rule on diameter, and that random beats
    # the screen on assignment -- the second is the negative result and is the easier of the
    # two to quietly flip into a positive one.
    for needle, why in [
        ("S beats C", "the direction of the diameter contrast against the constant rule"),
        (r"S \succ C \succ R", "the diameter ordering, stated as an ordering"),
        ("uniform billing is the", "the direction of the assignment loss"),
    ]:
        if needle not in src:
            problems.append(f"main.tex does not contain {needle!r} ({why})")

    problems += check_arm_d(d, src)
    problems += check_claim_scoped(d, src)
    problems += check_closure_floor(d, src)
    problems += check_withdrawn_phrases(src)

    gates = d["gates"]
    print(json.dumps({"record": str(rec), "gates": gates}, indent=1))
    if problems:
        print("E14 NUMBERS FAILED:", file=sys.stderr)
        for x in problems:
            print(f"  - {x}", file=sys.stderr)
        return 1
    print("E14 NUMBERS OK (main.tex agrees with results_e14.json, direction included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
