#!/usr/bin/env python3
"""Gate the two oracles' prices, and the ratio the paper is framed on, against a measurement.

The cost ratio between the liquid oracle and OpenFOAM is why this environment is worth building
at all: you cannot backpropagate CFD, so you differentiate the cheap coupled side and treat the
expensive side as an oracle.  A round-6 review found that neither price had a shipped record --
the air side was prose and the liquid side had never been timed in an artifact -- so the framing
device of the entire paper was unverifiable, and the quoted air range (46-72 s) did not match the
archived evaluations (46.0-75.6 s, n=42).

`records/oracle_costs.json` is written by
`ddl/case/benchB/E16_oracle_costs/measure_oracle_costs.py`, which harvests the air side from every
committed E8 record (real OpenFOAM runs, not re-measured) and times the liquid side over
E13/E14/E15's call sequence, forward-only and forward+backward, over several repeats with the
spread and the load average recorded.  Conditions travel with the measurement: a solve timed under
load is not the number a solve timed on an idle box gives, and quoting either without its
conditions is how "~10 ms" became uncheckable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def claim_scoped(src, specs, problems, where):
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for {label}, "
                            "so that number is unchecked -- an anchor that stops matching is a gate "
                            "that has gone dead, and the prose moving is the usual reason")
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", default=str(HERE / "records" / "oracle_costs.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp = Path(args.record)
    if not rp.exists():
        print(f"FAIL: no oracle-cost record at {rp}; the cost ratio the paper is framed on would "
              "be prose. Regenerate with "
              "ddl/case/benchB/E16_oracle_costs/measure_oracle_costs.py.", file=sys.stderr)
        return 2
    rec = json.loads(rp.read_text())
    src = Path(args.tex).read_text()
    problems = []

    L = rec.get("liquid_oracle") or {}
    A = rec.get("air_oracle") or {}
    R = rec.get("ratios") or {}
    P = rec.get("placement_price") or {}
    fo, fg = L.get("forward_only") or {}, L.get("forward_plus_adjoint") or {}

    # ---- the measurement must be a measurement ---------------------------------
    if (A.get("n_evaluations") or 0) < 20:
        problems.append(f"costs: only {A.get('n_evaluations')} archived air evaluations back the "
                        "air-side range; a range from fewer than 20 runs is an anecdote")
    if (L.get("repeats") or 0) < 3:
        problems.append(f"costs: the liquid side was measured {L.get('repeats')} time(s); two "
                        "consecutive runs of this script differed by 15% on a loaded box, so a "
                        "single measurement cannot support a ratio quoted to one significant "
                        "figure")
    if fg.get("repeat_spread_pct") is None:
        problems.append("costs: no repeat spread recorded, so the stability of the quoted ratio is "
                        "unknown")
    cond = (rec.get("provenance") or {}).get("conditions") or {}
    if not cond.get("loadavg_before"):
        problems.append("costs: no load average recorded with the measurement, so the number has "
                        "no conditions and cannot be reproduced or compared")
    ratio = R.get("air_median_over_liquid_forward_plus_adjoint")
    if ratio is None or not (1e3 <= ratio <= 1e4):
        problems.append(f"costs: the air/liquid ratio is {ratio}, outside the 1e3..1e4 band the "
                        "paper's '~5x10^3' claim needs; the framing number is not supported")
    if P.get("wall_clock_ratio") is None or P.get("solve_count_ratio") is None:
        problems.append("costs: the placement price is missing one of its two ratios; a count and "
                        "a price are different numbers and P11 requires the price beside the count")
    elif P["wall_clock_ratio"] >= P["solve_count_ratio"]:
        problems.append("costs: the wall-clock ratio exceeds the solve-count ratio, which is "
                        "impossible while one forward+backward costs more than one forward solve")

    fwd = f"{fo['median_ms']:.1f}"; grd = f"{fg['median_ms']:.0f}"
    specs = [
        (r"\$([0-9.]+)\$\\,ms per forward solve, \$([0-9.]+)\$\\,ms with gradient", (fwd, grd),
         "the liquid oracle's forward and forward+adjoint prices"),
        (r"OpenFOAM CFD \(\$(\d+)\$--\$(\d+)\$\\,s over \$(\d+)\$ archived evaluations\)",
         (f"{A['min_s']:.0f}", f"{A['max_s']:.0f}", A["n_evaluations"]),
         "the air oracle's range and how many archived evaluations it comes from"),
        (r"a cost ratio of \$\{\\sim\}(\d+)\\times10\^3\$",
         f"{ratio / 1e3:.0f}", "the air-to-liquid cost ratio the paper is framed on"),
        (r"so those \$(\d+)\$ central\s*differences cost\s*\$(\d+)\\times\$ one backward pass in wall "
         r"clock rather than \$(\d+)\\times\$",
         (P["solve_count_ratio"], f"{P['wall_clock_ratio']:.0f}", P["solve_count_ratio"]),
         "the placement price as both a solve count and a wall clock"),
        (r"an overhead of \$([0-9.]+)\\times\$", f"{R['adjoint_overhead_factor']:.1f}",
         "what one backward pass costs relative to one forward solve"),
    ]
    claim_scoped(src, specs, problems, "costs")

    if problems:
        print("COST GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print(f"costs agree with {rp.name}: liquid {fwd} ms forward / {grd} ms with gradient over "
          f"{L['repeats']} repeats ({fg['repeat_spread_pct']}% spread), air {A['min_s']:.0f}-"
          f"{A['max_s']:.0f} s over {A['n_evaluations']} archived evaluations, ratio {ratio:.0f}, "
          f"placement {P['solve_count_ratio']}x by count and {P['wall_clock_ratio']:.0f}x by clock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
