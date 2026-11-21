#!/usr/bin/env python3
"""Every tracked record has a reader, or it is a mirror that can go stale silently.

A review grepped scripts/ for readers of each JSON in records/ and found four with none:
closure_floor.json, e13_ranking.json, figure_diffs.json and e11d_d0_floors.json.  They were
committed as evidence, and nothing compared them with the manuscript, so a number in the
paper could drift from the record cited as its source and every gate would stay green.
closure_floor.json gained a reader when measure_e14.py started comparing the arm-D gap
against it; this reads the other three.

closure_floor.json is read HERE too, and claim-scoped rather than window-scoped, because
the one number in it that licenses the paper's headline is quoted in the ABSTRACT and a
window check cannot see it.  `expect()` asks whether a value appears within 300 characters
of a context word ANYWHERE in a 33-page document, so a tenfold error in the abstract's
`$1.6\times10^{-3}$` was satisfied by the six correct copies in the body and the suite
printed CHECK OK.  `claim_scoped()` below instead requires EVERY match of an anchored
pattern to equal the record, and treats zero matches as a failure, following
`measure_e13.py` and `measure_e14.py`.

Each check is claim-scoped: the value is read from the record and the manuscript is searched
for a statement of it, so a record that moves or a sentence that is reworded both fail rather
than passing quietly.  A record that is missing fails, because "the evidence is not here"
and "the evidence agrees" must not produce the same output.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
RECORDS = HERE / "records"


def load(name, problems):
    p = RECORDS / name
    if not p.exists():
        problems.append(f"records/{name} is tracked as evidence but is not present, so the "
                        "numbers it backs are unchecked")
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        problems.append(f"records/{name} does not parse: {e}")
        return None


WINDOW = 300


def expect(src, problems, needles, label, contexts):
    """Every value must appear near a word from the claim it belongs to.

    A bare substring test on a 33-page document is not a check: setting a record's n_pass
    from 12 to 11 passed because an eleven appears in the ledger and in a page reference.
    Requiring proximity to the claim's own vocabulary is what makes the match mean
    something, and the window is wide enough to survive hard-wrapped source.
    """
    windows = []
    low = src.lower()
    for c in contexts:
        i = 0
        cl = c.lower()
        while True:
            i = low.find(cl, i)
            if i < 0:
                break
            windows.append(low[max(0, i - WINDOW):i + WINDOW])
            i += len(cl)
    if not windows:
        problems.append(f"{label}: none of the context words {list(contexts)[:4]} appear in "
                        "main.tex, so no statement of these values can be located")
        return
    missing = [n for n in needles
               if n and not any(str(n) in w for w in windows)]
    if missing:
        problems.append(f"{label}: the manuscript does not state {missing[:4]} anywhere near "
                        f"the claim those values belong to (context: {list(contexts)[:4]})")
    else:
        print(f"  tracked records: {label} -- {len(needles)} value(s) stated near their claim")


SCI = r"([0-9.]+\\times10\^\{-?[0-9]+\})"


def latex_sci(x, sig=2):
    """Render a float the way main.tex renders it: 0.0015959 -> 1.6\\times10^{-3}.

    Two significant figures, because that is the precision every statement of the closure
    floor in this manuscript uses, and a gate that compared 0.0015958987039955694 against
    the string "1.6" would be comparing a record with a rounding of itself.
    """
    if not x:
        return "0"
    e = math.floor(math.log10(abs(x)))
    m = round(x / 10.0 ** e, sig - 1)
    if abs(m) >= 10:
        m /= 10.0
        e += 1
    return f"{m:.{sig - 1}f}\\times10^{{{e}}}"


def claim_scoped(src, specs, problems, where):
    """Every match of each anchored pattern must equal the value the record gives.

    specs: (pattern, expected, label).  `expected` may be a string, a number, a tuple (for
    a pattern with several groups) or a set of acceptable strings.  ZERO MATCHES IS A
    FAILURE: an anchored pattern that stops matching is a gate that has gone dead, and the
    prose moving is the usual reason.  This is the same helper `measure_e13.py` and
    `measure_e14.py` carry; it is duplicated here rather than imported because a gate that
    imports another gate's internals cannot be run, or reasoned about, on its own.
    """
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for "
                            f"{label} ({pattern!r}), so that number is unchecked -- the "
                            f"prose moved or the anchor is wrong, and a check with nothing "
                            f"to check is not a pass")
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
                            f"disagree with records/closure_floor.json: "
                            f"{sorted(set(map(str, bad)))[:4]} against {shown}")
        else:
            print(f"  tracked records: {label} -- all {len(hits)} statement(s) agree with "
                  f"the record")


def check_closure_floor_claims(src, problems):
    """Every stated closure-floor figure, against records/closure_floor.json.

    This is the check whose absence let a tenfold error in the abstract ship green: the
    record was read (by measure_e14.py, to bound the arm-D gap) but nothing compared the
    number the ABSTRACT quotes with it, and pdf_content_gate.probe() was deleting inline
    math before canon() could see it, so the rendered PDF was not checked either.
    """
    p = RECORDS / "closure_floor.json"
    if not p.exists():
        problems.append(f"records/{p.name} is tracked as evidence but is not present, so "
                        f"the closure floor the abstract is built on is unchecked")
        return
    d = json.loads(p.read_text())
    floor = d.get("closure_rel_gap_hard_feasible") or {}
    n = floor.get("n")
    # A check whose denominator can be zero must fail on zero.  The median of an empty
    # distribution is not a floor, and "n = 0" would otherwise satisfy every pattern below
    # vacuously the first time the measurement broke.
    if not n:
        problems.append(f"records/{p.name} reports n={n!r} for the hard-feasible closure "
                        f"gaps: a median over zero designs is not a floor, so nothing the "
                        f"manuscript quotes from it means anything")
        return
    missing = [k for k in ("median", "p90", "max") if floor.get(k) is None]
    if missing:
        problems.append(f"records/{p.name} has no closure_rel_gap_hard_feasible."
                        f"{'/'.join(missing)}, so the figure the manuscript quotes from "
                        f"it cannot be rendered")
        return
    med = latex_sci(floor["median"])
    p90 = latex_sci(floor["p90"])
    mx = latex_sci(floor["max"])
    cmp_ = d.get("comparison_to_manuscript") or {}
    ratios = {k: cmp_.get(k) for k in ("benchmark_median_ratio", "benchmark_max_ratio")}
    if any(v is None for v in ratios.values()):
        problems.append(f"records/{p.name} carries no comparison_to_manuscript ratios "
                        f"{ratios}, so the '13x / 76x the quoted value' claims are "
                        f"unchecked")
        return
    r_med = str(int(round(ratios["benchmark_median_ratio"])))
    r_max = str(int(round(ratios["benchmark_max_ratio"])))
    rows = d.get("n_rows")
    per_row = d.get("n_designs_per_row")

    specs = [
        (rf"median \${SCI}\$, worst \${SCI}\$", (med, mx),
         "the median/worst closure gap stated as a pair"),
        (rf"median \${SCI}\$ and worst \${SCI}\$", (med, mx),
         "the median/worst closure gap stated with 'and'"),
        (rf"median \${SCI}\$, \$90\$th percentile \${SCI}\$, maximum \${SCI}\$",
         (med, p90, mx), "the median / 90th percentile / maximum triple"),
        (rf"median \${SCI}\$ and maximum \${SCI}\$", (med, mx),
         "the median/maximum pair in the ledger"),
        (rf"closure floor of \${SCI}\$", med, "the closure floor, stated as a floor"),
        (rf"\${SCI}\$ closure floor", med, "the closure floor, stated before the noun"),
        (rf"floor of median \${SCI}\$", med, "the closure floor's median"),
        (r"over the \$(\d+)\$ feasible designs", str(n),
         "the number of feasible designs the floor is measured over"),
        (r"on the \$(\d+)\$ designs whose hard solve", str(n),
         "the number of designs whose hard solve converged"),
        (r"\$(\d+)\\times\$ and \$(\d+)\\times\$ the quoted value",
         (r_med, r_max), "the two ratios to the withdrawn single-seed floor"),
        (r"---\$(\d+)\\times\$ the quoted single-seed", r_med,
         "the median ratio to the withdrawn single-seed floor"),
        (r"the closure floor is \$(\d+)\\times\$ the quoted value at the benchmark "
         r"median", r_med, "the median ratio, restated"),
        (r"\$(\d+)\$ tasks \$\\times (\d+)\$ designs", (str(rows), str(per_row)),
         "the benchmark's shape"),
    ]
    claim_scoped(src, specs, problems, "closure floor")

    # The abstract is checked on its own, because it is the one place a reader who never
    # opens the body will take the number from, and because a window-scoped check cannot
    # tell an error there from a correct copy elsewhere.
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", src, re.S)
    if not m:
        problems.append("main.tex has no \\begin{abstract} block, so the abstract's "
                        "closure floor cannot be located")
        return
    ab = m.group(1)
    claim_scoped(ab, [
        (rf"median \${SCI}\$", med, "the ABSTRACT's median closure gap"),
        (rf"worst \${SCI}\$", mx, "the ABSTRACT's worst closure gap"),
        (r"over the \$(\d+)\$ feasible designs", str(n),
         "the ABSTRACT's feasible-design count"),
    ], problems, "abstract closure floor")


def main() -> int:
    problems = []
    tex = (HERE / "main.tex").read_text(encoding="utf-8")
    src = re.sub(r"\s+", " ", tex)

    e13 = load("e13_ranking.json", problems)
    if e13:
        blocks = e13.get("blocks") or {}
        want = []
        for name, key in (("diam", "rho_median"), ("place", "rho_median"),
                          ("assign", "rho_median")):
            b = blocks.get(name) or {}
            v = b.get(key)
            if v is None:
                problems.append(f"e13_ranking.json has no {name}.{key}")
                continue
            want.append(f"{float(v):.3f}")
            n = b.get("n_edits")
            if n:
                want.append(str(n))
        expect(src, problems, want, "E13's per-block rho and edit counts",
               ("rho", "neighbourhood", "e13", "edits"))
        prov = e13.get("provenance")
        if prov:
            # main.tex escapes underscores inside \texttt, so both spellings count; the
            # first run of this gate failed on that alone, which is a defect in the gate
            # and not in the manuscript.
            esc = prov.replace("_", r"\_")
            if prov not in tex and esc not in tex:
                problems.append(f"e13_ranking.json names its producer {prov} and main.tex "
                                "does not, so the record's provenance is not stated where "
                                "the numbers it backs are quoted")
            else:
                print(f"  tracked records: e13_ranking.json's producer is named in main.tex")

    check_closure_floor_claims(src, problems)

    fd = load("figure_diffs.json", problems)
    if fd:
        # Derive the per-kind spread of max channel difference from the pair rows, which is
        # what the manuscript quotes; n_pairs and n_pass are not quoted anywhere and asking
        # for them was answered by coincidental digits.
        kinds = {}
        for pr in fd.get("pairs") or []:
            k = pr.get("kind") or pr.get("label") or "?"
            v = pr.get("max_channel_diff")
            if isinstance(v, (int, float)):
                kinds.setdefault(k, []).append(v)
        want = []
        for k, vals in sorted(kinds.items()):
            if len(vals) > 1 and min(vals) != max(vals):
                want.append(f"${min(vals):g}$--${max(vals):g}$")
        bar = fd.get("determinism_bar") or {}
        if bar.get("threshold") is not None:
            want.append(str(bar["threshold"]))
        if not want:
            problems.append("figure_diffs.json carries no per-kind spread, so nothing about "
                            "the renderer's determinism can be checked")
        expect(src, problems, want,
               f"the renderer's per-pair channel spreads ({', '.join(sorted(kinds))})",
               ("figure", "plate", "pixel", "render", "channel"))
        if fd.get("gate_pass") is not True:
            problems.append("figure_diffs.json records gate_pass as not true, so the "
                            "manuscript's claim that the renderer separates the designs "
                            "it says it separates is unsupported")

    d0 = load("e11d_d0_floors.json", problems)
    if d0:
        floors = d0.get("floors_J_air") or {}
        want = []
        for key, fmt in (("e11c_pooled_replicate_sd", "{:.6g}"),
                         ("ratio_e8_over_e11c", "{:.1f}")):
            v = floors.get(key)
            if v is None:
                problems.append(f"e11d_d0_floors.json has no floors_J_air.{key}")
                continue
            want.append(fmt.format(float(v)))
        for side in ("on_e11c_floor", "on_e8_floor"):
            b = d0.get(side) or {}
            if b.get("max_z") is not None:
                want.append(f"{float(b['max_z']):.1f}")
        expect(src, problems, want, "the two D0 noise floors and the z each gives",
               ("floor", "d0", "noise", "replicate"))
        # The two floors disagree about the verdict and BOTH are on record, so the
        # manuscript must not present either as settled.
        v11 = (d0.get("on_e11c_floor") or {}).get("D0_pass")
        v8 = (d0.get("on_e8_floor") or {}).get("D0_pass")
        if v11 is not None and v8 is not None and bool(v11) != bool(v8):
            if "floor-dependent" not in src and "two noise floors" not in src:
                problems.append("e11d_d0_floors.json records the two floors giving OPPOSITE "
                                "D0 verdicts and main.tex says neither 'floor-dependent' nor "
                                "'two noise floors', so an unresolved verdict reads as a "
                                "resolved one")
            else:
                print("  tracked records: the two D0 floors disagree and main.tex says so")

    if problems:
        print("TRACKED RECORDS FAILED:", file=sys.stderr)
        for x in problems:
            print("  -", x, file=sys.stderr)
        return 1
    print("TRACKED RECORDS OK (every tracked record has a reader and agrees with main.tex)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
