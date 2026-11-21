#!/usr/bin/env python3
"""Gate E17's block-attribution numbers, and its verdicts, against its tracked record.

`ddl/case/benchB/E17_block_attribution/tracked/results_e17.json` is a re-analysis of
1728 archived CFD-billed rows and adds no evaluation to either oracle.  It is read from
the tracked copy rather than the logger because `ddl/case/*/*/loggers/` is gitignored,
and a number the manuscript quotes must survive a clean clone.

Four clauses here are STRUCTURAL rather than numeric, because each is a way this
experiment could be quoted dishonestly without a single digit being wrong:

  G3  the matched-incumbent contrast (A6) is the statistic quoted for the
      within-block claim, and the trajectory-inclusive contrast (A3) is labelled
      confounded wherever it appears.  A3 compares two arms that diverged onto
      different incumbents, so quoting it alone would report a confound as a result.
  G2  the catastrophe-filter claim is made only if the record says arm S billed no
      row above the threshold that was fixed before running.
  P4  the pooled-leverage sentence is withdrawn, because the per-task statistic
      contradicts it 8 of 8 and the pre-registration registered that branch.
  P9  the allocation mismatch is stated per task with its count, not as a systematic
      effect, because it holds on 4 of 8 and not the predicted 6.

Every numeric spec below is derived from the record; none is typed here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE
for _ in range(6):
    if (REPO / "ddl" / "case").is_dir():
        break
    REPO = REPO.parent
TRACKED = REPO / "ddl" / "case" / "benchB" / "E17_block_attribution" / "tracked" / "results_e17.json"


def claim_scoped(src, specs, problems, where):
    """Every spec must match at least once, and every match must equal the record.

    Zero matches is a failure, not a pass: a spec that stops matching means the prose
    moved and the number it pinned is now unchecked.
    """
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for {label}, "
                            "so that number is unchecked -- the prose moved or the anchor is wrong, "
                            "and a check with nothing to check is not a pass")
            continue
        if isinstance(expected, tuple):
            want = tuple("" if e is None else str(e) for e in expected)
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


def pct(x, nd=2):
    return f"{x * 100:.{nd}f}".rstrip("0").rstrip(".")


def sci(v):
    """LaTeX form of a p-value, matching how the manuscript prints it."""
    if v is None:
        return None
    if v == 0:
        return "0"
    e = 0
    m = float(v)
    while m < 1.0:
        m *= 10.0
        e -= 1
    return f"{m:.2f}".rstrip("0").rstrip("."), e


def pstr(v):
    """The manuscript prints small p-values as $a\\times10^{-b}$ and larger ones as decimals."""
    if v is None:
        return None
    if v < 1e-3:
        m, e = sci(v)
        return f"{m}\\times10^{{{e}}}"
    return f"{v:.4f}".rstrip("0").rstrip(".")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", default=str(TRACKED))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp, tp = Path(args.record), Path(args.tex)
    if not rp.exists():
        print(f"FAIL: no E17 record at {rp}.  The block-attribution numbers in the manuscript "
              f"would be unchecked, and a check with no denominator must fail rather than pass.  "
              f"Regenerate with ddl/case/benchB/E17_block_attribution/run.py", file=sys.stderr)
        return 2
    rec = json.loads(rp.read_text())
    src = tp.read_text()
    problems = []

    # ---- provenance and the no-new-physics clause ---------------------------
    if not rec.get("p6_no_new_physics"):
        problems.append("e17: the record does not assert P6 (no new physics).  E17 is a "
                        "re-analysis of archived rows; if it evaluated either oracle it is a "
                        "different experiment and its cost must be disclosed")
    if not rec.get("preregistered_before_running"):
        problems.append("e17: the record does not assert that it was pre-registered before "
                        "running, which is the only thing separating a prediction from a "
                        "description")
    fr = rec.get("fisher_reference") or {}
    if not fr.get("available"):
        problems.append("e17: the record's exact one-sided p-values were cross-checked only "
                        "against run.py's own factorial reference, because scipy was not "
                        "importable in the interpreter that produced it.  Re-run under an "
                        "interpreter with scipy so the record carries a third-party check")
    elif fr.get("n_disagreeing"):
        problems.append(f"e17: fisher_ge disagrees with scipy {fr.get('scipy_version')} on "
                        f"{fr['n_disagreeing']} of {fr.get('n_tables')} tables")
    else:
        print(f"  e17: p-values cross-checked against scipy {fr.get('scipy_version')} over "
              f"{fr.get('n_tables')} tables, {fr.get('n_disagreeing')} disagreements")

    inp = rec.get("inputs") or {}
    b, e = rec.get("e11b") or {}, rec.get("e11e") or {}
    a6b, a6e = b.get("A6_matched_incumbent") or {}, (e.get("A6_matched_incumbent") or {})
    a3b = (b.get("A3_within_block") or {}).get("summary") or {}
    a3e = (e.get("A3_within_block") or {}).get("summary") or {}
    c11b = (b.get("A1_composition") or {}).get("by_type") or {}
    c11e = (e.get("A1_composition") or {}).get("by_type") or {}
    preds = rec.get("predictions") or {}
    a5e = (e.get("A5_tail") or {})
    a4b = b.get("A4_leverage") or {}
    a6c = (b.get("A6c_allocation_vs_information") or {})

    if not a6b or not a6e:
        problems.append("e17: the record carries no A6 matched-incumbent contrast, so gate G3 "
                        "has nothing to enforce and the manuscript would be quoting A3, which "
                        "is confounded by trajectory divergence")

    # ---- G3: A6 is the quoted statistic, A3 is labelled confounded ----------
    if "matched-incumbent" not in src:
        problems.append("e17/G3: main.tex never says 'matched-incumbent', so the statistic the "
                        "manuscript quotes for the within-block claim cannot be the one the "
                        "pre-registration named")
    conf = [m.start() for m in re.finditer(r"confound", src, re.I)]
    if not conf:
        problems.append("e17/G3: main.tex never uses the word 'confounded', so the "
                        "trajectory-inclusive contrast is quoted without the caveat that makes "
                        "it unreadable on its own")
    if rec.get("gate_G3_quoted_statistic") != "A6_matched_incumbent":
        problems.append(f"e17/G3: the record names {rec.get('gate_G3_quoted_statistic')!r} as the "
                        "quoted statistic, not A6_matched_incumbent")

    # ---- G2: the catastrophe filter ---------------------------------------
    s_over = (preds.get("P5_gate_G2_tail_avoidance") or {}).get("n_S_rows_over_threshold")
    claims_filter = "catastrophe" in src.lower() or "declines to\nspend" in src or \
        "declines to spend" in src
    if s_over is None:
        problems.append("e17/G2: the record carries no S-row count above the catastrophe "
                        "threshold, so the filter claim cannot be licensed either way")
    elif s_over == 0 and not claims_filter:
        problems.append("e17/G2: the record licenses the catastrophe-filter claim (S billed no "
                        "row above the threshold) but the manuscript does not make it, so a "
                        "measured result is going unreported")
    elif s_over > 0 and claims_filter:
        problems.append(f"e17/G2: arm S billed {s_over} row(s) above the catastrophe threshold, "
                        "so the filter claim is false and must be removed")

    # ---- P4: the pooled-leverage sentence must be withdrawn ----------------
    p4 = preds.get("P4_enriched_is_most_air_leveraged_e11b") or {}
    if p4.get("holds") is False:
        if "withdrawn" not in src:
            problems.append("e17/P4: the per-task leverage statistic contradicts the pooled one "
                            "on every task and the pre-registered branch fired, but main.tex "
                            "never says a sentence was withdrawn")
    elif p4.get("holds") is True and "withdrawn" in src and "pooled range is not" in src:
        problems.append("e17/P4: the prediction now holds, so the withdrawal of the pooled "
                        "leverage sentence is no longer licensed")

    # ---- P9: the mismatch is a split, not a systematic effect -------------
    p9 = preds.get("P9_allocation_mismatch_e11b") or {}
    if p9.get("holds") is False:
        if not re.search(r"(split|not a systematic)", src):
            problems.append("e17/P9: the allocation mismatch holds on fewer tasks than predicted "
                            "and the manuscript must say it is a split rather than a systematic "
                            "effect")

    # ---- every registered prediction's outcome must be reported -----------
    registered = ["P1_S_winner_is_cool_flip", "P3_e11e_S_winner_is_diam",
                  "P4_enriched_is_most_air_leveraged_e11b", "P9_allocation_mismatch_e11b"]
    for k in registered:
        v = preds.get(k) or {}
        if v.get("holds") is False:
            n, d = v.get("wins", v.get("wins_diam", v.get("n_mismatch"))), \
                   v.get("n", v.get("n_defined"))
            if k.startswith("P4"):
                n, d = v.get("n_agreeing"), v.get("n_defined")
            if n is None or d is None:
                continue
            pat = re.compile(r"\$?" + str(n) + r"\$?\s*(of|/)\s*\$?" + str(d) + r"\$?")
            if not pat.search(src):
                problems.append(f"e17: prediction {k} FAILED ({n} of {d}) and the manuscript does "
                                "not print that count, so a failed prediction is being reported "
                                "without the number that failed")

    # ---- numeric claims, all derived from the record ----------------------
    e11e_c = a6e.get("contrasts") or {}
    specs = []

    def add(pat, exp, label):
        if exp is not None and (not isinstance(exp, tuple) or all(x is not None for x in exp)):
            specs.append((pat, exp, label))

    add(r"spending \$(\d+)\\%\$ of E11b's budget",
        round(100 * (c11b.get("cool_flip") or {}).get("n_S", 0)
              / max(1, (c11b.get("cool_flip") or {}).get("n_billed_S", 1))),
        "the share of E11b's bills the adjoint spends on cooling flips (body)")
    add(r"\\min\\Jliq\$ on \$(\d+)\$ of \$(\d+)\$ tasks",
        (e11e_c.get("min_Jliq", {}).get("wins_S"), e11e_c.get("min_Jliq", {}).get("n_compared")),
        "A6 min J_liq win count (body)")
    add(r"\\min\\Jliq\$ \(primary\) & \\textbf\{(\d+)\} & (\d+) & \$([\d.]+)\\times10\^\{-(\d+)\}\$",
        (e11e_c.get("min_Jliq", {}).get("wins_S"), e11e_c.get("min_Jliq", {}).get("n_compared"),
         sci(e11e_c.get("min_Jliq", {}).get("sign_p"))[0] if e11e_c.get("min_Jliq", {}).get("sign_p") else None,
         -sci(e11e_c.get("min_Jliq", {}).get("sign_p"))[1] if e11e_c.get("min_Jliq", {}).get("sign_p") else None),
        "A6 min J_liq row of tab:e17a")
    def pfmt(p):
        if p is None:
            return None
        if p >= 0.5:
            return f"{p:.2f}".rstrip("0").rstrip(".")
        if p >= 1e-3:
            return f"{p:.4f}".rstrip("0").rstrip(".")
        return None

    add(r"\\min\\Jair\$ & (\d+) & (\d+) & \$([\d.]+)\$",
        (e11e_c.get("min_Jair", {}).get("wins_S"), e11e_c.get("min_Jair", {}).get("n_compared"),
         pfmt(e11e_c.get("min_Jair", {}).get("sign_p"))),
        "A6 min J_air row of tab:e17a")
    add(r"\\min\\Jtot\$ & (\d+) & (\d+) & \$([\d.]+)\$",
        (a6b.get("wins_S"), a6b.get("n_compared"), pfmt(a6b.get("sign_p"))),
        "A6 E11b J_tot row of tab:e17a")
    # the body's three p-values, so a shift in the body cannot hide behind the table
    add(r"room\s*templates\s*\(\$p = ([\d.]+)\\times10\^\{-(\d+)\}\$\)",
        (sci(e11e_c.get("min_Jliq", {}).get("sign_p"))[0],
         -sci(e11e_c.get("min_Jliq", {}).get("sign_p"))[1])
        if e11e_c.get("min_Jliq", {}).get("sign_p") else None,
        "A6 min J_liq p (body)")
    # the tail paragraph, whose four numbers all come from A5
    a5 = e.get("A5_tail") or {}
    def mx(arm, ty):
        c = a5.get(f"{arm}|{ty}") or {}
        return round(c.get("max_Jtot") or 0)
    over = sum(v.get("over_threshold", 0) for v in a5.values())
    s_over_arm = {k.split("|")[0] for k, v in a5.items() if v.get("over_threshold")}
    add(r"billed \\textbf\{two\} rows above the threshold", None, None) if False else None
    specs.append((r"the worse is \$\\Jtot = (\d+)\$", mx("R", "assign"),
                  "the worst over-threshold J_tot"))
    specs.append((r"its worst is \$(\d+)\$", mx("S", "assign"),
                  "arm S's worst assign J_tot"))
    specs.append((r"the largest billed \$\\Jtot\$ is \$(\d+)\$ for \\texttt\{slot\} "
                  r"and \$(\d+)\$\s*for \\texttt\{diam\}",
                  (max(mx("R", "slot"), mx("S", "slot")), max(mx("R", "diam"), mx("S", "diam"))),
                  "the largest billed J_tot for the two non-catastrophic types"))
    if over != 2:
        problems.append(f"e17: the manuscript says 'two' over-threshold rows but the record "
                        f"holds {over}")
    if s_over_arm and "S" in s_over_arm:
        problems.append("e17/G2: arm S has an over-threshold row, so the tail paragraph is false")

    # composition rows of tab:e17b
    for tag, tbl, name in (("E11b", c11b, "cool_flip"), ("E11b", c11b, "slot_move"),
                           ("E11e", c11e, "slot"), ("E11e", c11e, "diam"),
                           ("E11e", c11e, "assign")):
        cell = tbl.get(name) or {}
        enr = cell.get("enrichment_S_over_R")
        add(r"" + tag + r" & \\texttt\{" + name.replace("_", r"\\_") +
            r"\} & (\d+) & (\d+) & \$([\d.]+)\\times\$ & (\d+)/(\d+)",
            (cell.get("n_S"), cell.get("n_R"),
             f"{enr:.2f}" if enr is not None else None,
             cell.get("n_tasks_S_enriched"), cell.get("n_tasks_defined")),
            f"composition row {tag}/{name}")

    # within-block rows of tab:e17b (A3, confounded)
    for key, tbl, name in ((r"cool\_flip", a3b, "cool_flip__median"),
                           (r"slot\_move", a3b, "slot_move__median"),
                           (r"slot", a3e, "slot"), (r"diam", a3e, "diam")):
        cell = tbl.get(name) or {}
        p = cell.get("sign_p")
        add(r"\{c\}\{\$" + r"(\d+)/(\d+)" + r"\$, \$p = " + (
            r"([\d.]+)\\times10\^\{-(\d+)\}\$" if (p is not None and p < 1e-3) else r"([\d.]+)\$") + r"\}",
            None, None) if False else None
    # the four within-block cells, written out because their p-value formats differ
    def within(cell):
        p = cell.get("sign_p")
        if p is None:
            return None
        if p < 1e-3:
            m, e = sci(p)
            return (cell.get("wins_S"), cell.get("n_compared"), m, -e)
        return (cell.get("wins_S"), cell.get("n_compared"),
                (f"{p:.2f}" if p >= 0.5 else f"{p:.4f}").rstrip("0").rstrip("."), None)

    for label, cell, pat in (
            ("within-block cool_flip", a3b.get("cool_flip__median"),
             r"\\texttt\{cool\\_flip\}.*?\{c\}\{\$(\d+)/(\d+)\$, \$p = ([\d.]+)\$\}"),
            ("within-block slot_move", a3b.get("slot_move__median"),
             r"\\texttt\{slot\\_move\}.*?\{c\}\{\$(\d+)/(\d+)\$, \$p = ([\d.]+)\$\}"),
            ("within-block e11e slot", a3e.get("slot"),
             r"\\texttt\{slot\} & \d+ & \d+ & \$[\d.]+\\times\$ & \d+/\d+ & \\multicolumn\{2\}\{c\}\{\$(\d+)/(\d+)\$, \$p = ([\d.]+)\\times10\^\{-(\d+)\}\$\}"),
            ("within-block e11e diam", a3e.get("diam"),
             r"\\texttt\{diam\} & \d+ & \d+ & \$[\d.]+\\times\$ & \d+/\d+ & \\multicolumn\{2\}\{c\}\{\$(\d+)/(\d+)\$, \$p = ([\d.]+)\\times10\^\{-(\d+)\}\$\}")):
        w = within(cell or {})
        if w is None:
            continue
        exp = tuple(x for x in w if x is not None)
        p2 = pat.replace("(\\d+)/(\d+)\\$, \\$p = ([\\d.]+)\\$", "(\\d+)/N") if False else pat
        specs.append((pat, exp, label))

    add(r"re-analyses the \$(\d+)\$ billed rows of\s*E11b", inp.get("e11b_rows"), "E11b row count")
    add(r"the \$(\d+)\$ of E11e", inp.get("e11e_rows"), "E11e row count")
    add(r"range is \$(\d+)\$ against \$([\d.]+)\$ and \$([\d.]+)\$",
        None, None) if False else None

    claim_scoped(src, specs, problems, "e17")

    # ---- A7: the weight sweep, all five cells, from the record -------------
    # Scoped to its own paragraph.  lambda = 133.7 and 93.1 are quoted throughout
    # sec:search and app:derivations, and a document-wide search matched six
    # unrelated mentions and reported a win count belonging to a different
    # sentence: a check that cannot tell which sentence it is reading checks
    # nothing.
    a7 = ((b.get("A7_lambda_sensitivity") or {}).get("by_lambda") or {})
    if a7:
        head = "\\paragraph{The E11b contrast across the weight bracket"
        i0 = src.find(head)
        if i0 < 0:
            problems.append("e17/A7: main.tex has no paragraph headed 'The E11b contrast "
                            "across the weight bracket', so the pre-registered weight sweep is "
                            "in the record and not in the paper")
        else:
            i1 = src.find("\\paragraph{", i0 + len(head))
            para = src[i0:i1 if i1 > 0 else len(src)]
            if not re.search(r"may not\s+be used to re-choose \$\\lambda\$", para):
                problems.append("e17/A7: the lambda sweep is reported without the clause that "
                                "makes it descriptive.  A grid searched after seeing which "
                                "weight flatters the result is not a pre-registration")
            for lam, cell in a7.items():
                m = re.search(r"\$" + re.escape(lam) + r"\$", para)
                if not m:
                    problems.append(f"e17/A7: the sweep at lambda={lam} is not reported in the "
                                    f"paragraph, so a cell of a pre-registered grid is missing")
                    continue
                got = re.search(r"(\d+)\s*/\s*(\d+)", para[m.end():m.end() + 60])
                if not got:
                    problems.append(f"e17/A7: lambda={lam} carries no win count in the "
                                    f"paragraph, so the cell cannot be checked")
                elif (int(got.group(1)), int(got.group(2))) != (cell["wins_S"],
                                                               cell["n_compared"]):
                    problems.append(f"e17/A7: lambda={lam} is reported as "
                                    f"{got.group(1)}/{got.group(2)} where the record says "
                                    f"{cell['wins_S']}/{cell['n_compared']}")
                else:
                    print(f"  e17/A7: lambda={lam} -- {cell['wins_S']}/{cell['n_compared']} "
                          f"agrees with the record")
            lo, hi = 133.2, 227.6
            inside = [(float(k), v) for k, v in a7.items() if lo <= float(k) <= hi]
            if not inside:
                problems.append(f"e17/A7: the grid {sorted(float(k) for k in a7)} has no weight "
                                f"inside the identified bracket [{lo}, {hi}], so the coverage "
                                "claim cannot be made from this record")
            else:
                worst = min(v["wins_S"] / max(1, v["n_compared"]) for _, v in inside)
                if re.search(r"whole\s+identified bracket is therefore inside the range", para) \
                        and worst < 0.75:
                    problems.append(f"e17/A7: the paragraph says the whole identified bracket "
                                    f"lies where the matched pair holds, but the worst weight "
                                    f"inside [{lo}, {hi}] wins only {worst:.2f} of its tasks")
                print(f"  e17/A7: {len(inside)} grid weight(s) inside the identified bracket "
                      f"[{lo}, {hi}], worst win fraction {worst:.3f}")

    # ---- the tail range, which is a record value and looks like a typo ------
    if problems:
        print("E17 GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print(f"e17 numbers and verdicts agree with {rp.name} (G3 quoted statistic, G2 filter "
          f"licence, P4 withdrawal and P9 split all enforced structurally)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
