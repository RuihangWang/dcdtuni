#!/usr/bin/env python3
"""Derive the epsilon-constraint win count at BOTH units, from the records.

A15.4 amended the gate's unit from the task-tau CELL to the TASK, because the
taus of one task are nested and correlated, so a cell-level sign test treats them
as independent and is anti-conservative; it also wrote that run.py's cell-level
number "is not the statistic and must not be quoted as one".  The manuscript
quoted it anyway, in the abstract and in section 5, as 19/24 (p = 0.0033) and
36/40 (p = 9.3e-8).  This script exists so the corrected headline is derived
rather than typed, and so both units stay visible side by side: the cell-level
number is not wrong arithmetic, it is the wrong unit, and reporting only the
favourable one would be the same mistake in the other direction.

Definitions, both from A15.4 / A11.5:
  cell   for one (task, tau): each arm's cheapest J_liq among its billed designs
         with J_air <= tau.  Undefined for an arm with no feasible design.
         S wins the cell when both arms are defined and S's value is lower.
  task   S wins the task when it wins EVERY tau that is defined for both arms.
         A task with no tau defined for both arms is not a win and not a loss:
         it is uninformative, and is reported separately rather than folded into
         either denominator.

Two readings of an undefined cell are both reported, because they differ and the
choice is not neutral:
  drop   cells where either arm is undefined are excluded (the favourable one)
  loss   they count as losses for S (the conservative one, and the one the
         manuscript's 19/24 and 36/40 used)

Nothing is typed.  Every count and p comes from the archive named on the command
line, and the exact one-sided binomial sign test is computed from binomials, not
from a normal approximation.

THIS FILE HAD NO CALL SITE, and the manuscript cites it by name: Appendix C says "the
committed `scripts/verify_eps_units.py` derives every count and p at both units and both
readings of an undefined tau".  A script a paper cites as the source of its numbers, which
no gate runs, is the dead-check defect this repository keeps naming -- and it is worse than
dead, because a reader who runs it by hand gets numbers nobody has compared with the paper.
So it grew a `--check` mode, which is the part `check_manuscript.py` wires in: it re-derives
every count and p and pins each one at the sentence that states it, with zero matches
treated as a failure the way `claim_scoped()` in measure_e13.py and measure_e14.py does.

`--check` reads the COMMITTED snapshot of E11b's archive
(`records/campaign_snapshot/...results_e11b.json`), not the live gitignored one, so it is
green on a clean clone.  When both are present they are compared, because a tracked copy
nobody cross-checks is a mirror that can go stale silently.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from math import comb
from pathlib import Path

LAUNCHED = (2.029, 2.108, 2.211, 2.360, 2.528)

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
SNAPSHOT_NAME = ("ddl__case__benchB__E11b_surrogate_search__loggers__e11b_full__"
                 "results_e11b.json")
TRACKED = HERE / "records" / "campaign_snapshot" / SNAPSHOT_NAME
LIVE = (REPO / "ddl" / "case" / "benchB" / "E11b_surrogate_search" / "loggers"
        / "e11b_full" / "results_e11b.json")


def sign_p_one_sided(wins: int, n: int) -> float:
    """Exact one-sided binomial sign test, P(X >= wins) at p = 1/2."""
    if n <= 0:
        return float("nan")
    return sum(comb(n, k) for k in range(wins, n + 1)) / (2.0 ** n)


def pooled_percentile_taus(tasks: list, qs=(25, 50, 75)) -> tuple:
    """The pooled percentiles of realised J_air over every stored evaluation."""
    vals = sorted(r["J_air"] for t in tasks for a in t["arms"].values()
                  for r in a["rows"] if r.get("J_air") is not None)
    if not vals:
        raise SystemExit("no J_air values in the archive")
    out = []
    for q in qs:
        # nearest-rank percentile on the pooled sample
        idx = max(0, min(len(vals) - 1, int(round(q / 100.0 * len(vals) + 0.5)) - 1))
        out.append(vals[idx])
    return tuple(out), len(vals)


def eps_value(rows: list, tau: float):
    """Cheapest J_liq among this arm's billed designs with J_air <= tau."""
    feas = [r["J_liq"] for r in rows
            if r.get("J_air") is not None and r["J_air"] <= tau]
    return min(feas) if feas else None


def cell_table(tasks: list, taus, arms=("S", "R")):
    """Per (task, tau) outcomes, plus the per-tau and per-task rollups."""
    A, B = arms
    cells = []
    for t in tasks:
        seed = t["seed"]
        ra = t["arms"].get(A, {}).get("rows", [])
        rb = t["arms"].get(B, {}).get("rows", [])
        for tau in taus:
            va, vb = eps_value(ra, tau), eps_value(rb, tau)
            defined = va is not None and vb is not None
            if defined:
                outcome = "win" if va < vb else ("tie" if va == vb else "loss")
            else:
                outcome = "undefined"
            cells.append({"seed": seed, "tau": tau, "S": va, "R": vb,
                          "defined": defined, "outcome": outcome})
    return cells


def rollup(cells: list, taus, undefined: str):
    """Cell-level and task-level win counts under one undefined-reading."""
    per_tau = {}
    for tau in taus:
        cs = [c for c in cells if c["tau"] == tau]
        w = sum(1 for c in cs if c["outcome"] == "win")
        d = sum(1 for c in cs if c["defined"])
        n = d if undefined == "drop" else len(cs)
        per_tau[tau] = {"wins": w, "defined": d, "cells": len(cs), "n": n}
    cw = sum(v["wins"] for v in per_tau.values())
    cn = sum(v["n"] for v in per_tau.values())

    # task level: S must win every tau defined for both arms
    tasks = {}
    for c in cells:
        tasks.setdefault(c["seed"], []).append(c)
    tw = tl = tu = 0
    losing = []
    for seed, cs in sorted(tasks.items()):
        defined = [c for c in cs if c["defined"]]
        if not defined:
            tu += 1                      # uninformative: reported, not folded in
            continue
        wins = sum(1 for c in defined if c["outcome"] == "win")
        if undefined == "loss":
            # an undefined tau is charged against S, so it must win those too
            wins += 0
            all_cells = len(cs)
            ok = wins == all_cells
        else:
            ok = wins == len(defined)
        if ok:
            tw += 1
        else:
            tl += 1
            losing.append({"seed": seed, "wins": wins, "defined": len(defined),
                           "cells": len(cs)})
    tn = tw + tl
    return {"undefined_reading": undefined,
            "per_tau": {str(k): v for k, v in per_tau.items()},
            "cell": {"wins": cw, "n": cn, "p": sign_p_one_sided(cw, cn)},
            "task": {"wins": tw, "n": tn, "p": sign_p_one_sided(tw, tn),
                     "uninformative_tasks": tu, "not_won": losing}}


def latex_sci(x, sig=2):
    """9.28512e-08 -> '9.3\\times10^{-8}', the form the appendix uses."""
    if not x:
        return "0"
    e = math.floor(math.log10(abs(x)))
    m = round(x / 10.0 ** e, sig - 1)
    if abs(m) >= 10:
        m /= 10.0
        e += 1
    return f"{m:.{sig - 1}f}" + "\\times10^{" + f"{e}" + "}"


def claim_scoped(src, specs, problems, where):
    """Every match of each anchored pattern must equal the derived value.

    Zero matches is a FAILURE: an anchored pattern that stops matching is a gate that has
    gone dead, and the prose moving is the usual reason.
    """
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for "
                            f"{label} ({pattern!r}), so that number is unchecked -- the "
                            f"prose moved or the anchor is wrong, and a check with nothing "
                            f"to check is not a pass")
            continue
        def okset(e):
            """A single value, or the set of renderings of it the manuscript may use."""
            if isinstance(e, (set, frozenset)):
                return {str(x) for x in e}
            return {str(e)}

        if isinstance(expected, tuple):
            want = [okset(e) for e in expected]
            bad = [h for h in hits if any(g not in w for g, w in zip(h, want))]
            shown = " / ".join(sorted(w)[0] for w in want)
        else:
            ok = okset(expected)
            flat = [h if isinstance(h, str) else " / ".join(h) for h in hits]
            bad = [h for h in flat if h not in ok]
            shown = ", ".join(sorted(ok)[:4])
        if bad:
            problems.append(f"{where}: {len(bad)} of {len(hits)} statements of {label} "
                            f"disagree with what this script derives: "
                            f"{sorted(set(map(str, bad)))[:4]} against {shown}")
        else:
            print(f"  eps-units: {label} -- all {len(hits)} statement(s) agree with the "
                  f"derived value")


FR = r"\$(\d+)/(\d+)\$"          # wins and n as SEPARATE groups
FR1 = r"\$(\d+/\d+)\$"             # one fraction as ONE group, for a per-tau list


def sci_parts(x):
    """(mantissa, exponent) of the LaTeX scientific rendering this manuscript uses."""
    m = re.match(r"([0-9.]+)\\times10\^\{(-?[0-9]+)\}", latex_sci(x))
    if not m:
        raise SystemExit(f"cannot render {x!r} as LaTeX scientific notation")
    return m.group(1), m.group(2)


def check(out, tex: Path, problems=None) -> int:
    """Pin every count and p this script derives at the sentence that states it.

    Each expected value comes from the derivation above and nothing is transcribed from
    the manuscript, which is the difference between a gate and a mirror.  The two grids
    and the two readings of an undefined tau are kept apart by name, because the body's
    `$19/24$` is the POOLED grid with an undefined tau charged as a loss while its
    `$36/40$` is the LAUNCHED grid under the same reading -- and the drop-undefined
    readings of the same two grids are 19/20 and 36/38, which is precisely the confusion
    that would let a wrong denominator through.
    """
    problems = [] if problems is None else problems
    src = re.sub(r"\s+", " ", tex.read_text(encoding="utf-8"))
    L = out["by_grid"]["launched"]
    P = out["by_grid"]["pooled_percentiles"]
    ld, ll = L["drop_undefined"], L["undefined_as_losses"]
    pd_, pl = P["drop_undefined"], P["undefined_as_losses"]

    if out["n_stored_evaluations"] <= 0:
        problems.append(f"the archive carries {out['n_stored_evaluations']} stored "
                        f"evaluations, so there is no denominator: a win count over zero "
                        f"cells is not a result and must not pass vacuously")
        return problems

    def tau_counts(roll):
        return tuple(f"{v['wins']}/{v['n']}" for v in roll["per_tau"].values())

    lm, le = sci_parts(ll["cell"]["p"])
    specs = [
        # the per-tau counts, in the order the body lists them (drop-undefined reading:
        # "the denominator counting only tasks where both arms have a feasible design")
        (r"screen wins " + (FR1 + r", ") * 4 + FR1 + r" on the launched grid",
         tau_counts(ld), "the per-tau win counts on the launched grid"),
        (r"and " + (FR1 + r", ") * 2 + FR1 + r" on the pooled one",
         tau_counts(pd_), "the per-tau win counts on the pooled grid"),
        # the two withdrawn cell-level numbers, as the body quotes them
        (r"cell-level.{0,40}?" + FR + r" and " + FR,
         (str(pl["cell"]["wins"]), str(pl["cell"]["n"]),
          str(ll["cell"]["wins"]), str(ll["cell"]["n"])),
         "the two withdrawn CELL-level counts in the body"),
        # the task-level statistic, identical on both grids under the drop reading
        (r"task level the same archive gives \\textbf\{" + FR + r", \$p = ([\d.]+)\$\}",
         (str(ld["task"]["wins"]), str(ld["task"]["n"]),
          {f"{ld['task']['p']:.4f}", f"{ld['task']['p']:.2f}"}),
         "the pre-registered TASK-level count and its p"),
        # the conservative reading, both grids
        (r"gives " + FR + r" \(\$p = ([\d.]+)\$\) pooled and " + FR
         + r" \(\$p = ([\d.]+)\$\) launched",
         (str(pl["task"]["wins"]), str(pl["task"]["n"]),
          {f"{pl['task']['p']:.2f}", f"{pl['task']['p']:.4f}"},
          str(ll["task"]["wins"]), str(ll["task"]["n"]),
          {f"{ll['task']['p']:.2f}", f"{ll['task']['p']:.4f}"}),
         "the task-level sensitivity to reading an undefined tau as a loss"),
        (r"the task that does not win is (\d+)",
         str(min((x["seed"] for x in ld["task"]["not_won"]), default=-1)),
         "the seed of the task S does not win"),
        # the appendix restates both withdrawn cell-level numbers with their p
        (r"quoted it anyway, in the body and in the abstract, as " + FR
         + r", \$p = ([\d.]+)\$ \(pooled grid",
         (str(pl["cell"]["wins"]), str(pl["cell"]["n"]),
          {f"{pl['cell']['p']:.4f}", f"{pl['cell']['p']:.2f}"}),
         "the withdrawn pooled CELL-level count and its p, restated in the appendix"),
        (r"\) and " + FR + r",\s*\$p = ([0-9.]+)\\times10\^\{(-?[0-9]+)\}\$ \(launched\)",
         (str(ll["cell"]["wins"]), str(ll["cell"]["n"]), lm, le),
         "the withdrawn launched CELL-level count and its p, restated in the appendix"),
        (r"reproduce exactly from the (\d+) pairs", str(out["n_stored_evaluations"]),
         "the number of stored pairs both withdrawn counts reproduce from"),
    ]
    claim_scoped(src, specs, problems, "eps-units")
    return problems


def resolve_results(arg):
    """The archive to read: --results, else the TRACKED snapshot, else the live one."""
    if arg:
        p = Path(arg)
        if not p.exists():
            raise SystemExit(f"--results {p} does not exist")
        return p, []
    problems = []
    if TRACKED.exists():
        if LIVE.exists():
            a = json.loads(TRACKED.read_text())
            b = json.loads(LIVE.read_text())
            if a != b:
                problems.append(f"the tracked snapshot {SNAPSHOT_NAME} disagrees with the "
                                f"live {LIVE.relative_to(REPO)}, so the copy the manuscript "
                                f"is checked against is stale; re-run `make snapshot`")
        return TRACKED, problems
    if LIVE.exists():
        return LIVE, [f"reading the GITIGNORED {LIVE.relative_to(REPO)}: there is no "
                      f"tracked snapshot, so a clean clone cannot run this check"]
    raise SystemExit(f"no E11b archive: neither {TRACKED} nor {LIVE} exists")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=None,
                    help="an E11b/E11e results_*.json with tasks[].arms[].rows; defaults "
                         "to the committed snapshot, which is what makes --check hermetic")
    ap.add_argument("--arms", default="S,R")
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true",
                    help="derive every count and p and pin it at the sentence in main.tex "
                         "that states it")
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rec_path, rec_problems = resolve_results(args.results)
    args.results = str(rec_path)
    d = json.loads(Path(args.results).read_text())
    tasks = d["tasks"]
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    arms = tuple(args.arms.split(","))
    pooled, n_eval = pooled_percentile_taus(tasks)

    out = {"archive": str(rec_path),
           "n_tasks": len(tasks), "arms": list(arms),
           "n_stored_evaluations": n_eval,
           "grids": {"launched": list(LAUNCHED), "pooled_percentiles": list(pooled)},
           "by_grid": {}}
    for gname, taus in (("launched", LAUNCHED), ("pooled_percentiles", pooled)):
        cells = cell_table(tasks, taus, arms)
        out["by_grid"][gname] = {
            "cells": cells,
            "drop_undefined": rollup(cells, taus, "drop"),
            "undefined_as_losses": rollup(cells, taus, "loss"),
        }

    # the numbers the manuscript quoted, reproduced so the correction is a
    # restatement against the same arithmetic rather than a different statistic
    print(json.dumps({k: v for k, v in out.items() if k != "by_grid"}, indent=1))
    for gname, g in out["by_grid"].items():
        print(f"\n--- grid {gname}: {g and [round(t,4) for t in out['grids'][gname]]}")
        for reading in ("drop_undefined", "undefined_as_losses"):
            r = g[reading]
            print(f"  {reading}:")
            print(f"    per-tau wins/n: "
                  + ", ".join(f"{v['wins']}/{v['n']}" for v in r["per_tau"].values()))
            c, t = r["cell"], r["task"]
            print(f"    CELL-level (the unit A15.4 forbids quoting): "
                  f"{c['wins']}/{c['n']}  p = {c['p']:.6g}")
            print(f"    TASK-level (the statistic): "
                  f"{t['wins']}/{t['n']}  p = {t['p']:.6g}"
                  f"  uninformative tasks = {t['uninformative_tasks']}")
            if t["not_won"]:
                print(f"    tasks S did not win: "
                      + ", ".join(f"{x['seed']} ({x['wins']}/{x['defined']} defined)"
                                  for x in t["not_won"]))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {args.out}")

    if args.check:
        problems = list(rec_problems)
        check(out, Path(args.tex), problems)
        if problems:
            print("EPS-UNITS CHECK FAILED:", file=sys.stderr)
            for x in problems:
                print("  -", x, file=sys.stderr)
            return 1
        print(f"EPS-UNITS CHECK OK (every count and p the manuscript quotes is derived "
              f"here from {rec_path.name}, at both units and both readings of an "
              f"undefined tau)")
        return 1 if rec_problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
