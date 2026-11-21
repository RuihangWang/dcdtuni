"""Compare Base604 lower-bound variants against a reference by the registered gate (GATE.md).

    python compare.py REF_result.json VARIANT_result.json [...]

Criteria (all must hold, variant vs reference): both CONVERGED; every ACU
return-air temperature within 0.5 K; rack inlet temperature (API rackInlet,
per rack) mean abs diff <= 0.5 K, 95th percentile <= 1.0 K, max <= 2.0 K;
both heat ledgers (ACU heat removed vs declared IT power) within 1 %.
"""
import json
import re
import sys
from pathlib import Path


def load(path):
    d = json.loads(Path(path).read_text())
    m = d["metrics"]
    f = m["fields"]
    run = Path(f["runDir"])
    log = next(run.glob("*.log"))
    text = log.read_text(errors="replace")
    t = [float(x) for x in re.findall(r"ExecutionTime = ([\d.]+) s", text)]
    ranks = int(re.search(r"mpirun -np (\d+)", text).group(1))
    cells = 0
    for owner in run.glob("processor*/constant/polyMesh/owner"):
        with open(owner, errors="replace") as fh:
            head = fh.read(2000)
        cells += int(re.search(r"nCells:\s*(\d+)", head).group(1))
    return {
        "name": Path(d["caseDir"]).name,
        "verdict": m["convergence"]["verdict"],
        "iterations": m["convergence"]["residuals"]["iterations"],
        # An ACU whose return is a box patch carries no "return" block here.
        "acu": {a["returnPatch"]: a["return"]["areaMeanK"] for a in f["acus"] if a.get("return")},
        "rack": {r["name"]: r["areaMeanK"] for r in ((f.get("rackInlet") or {}).get("racks") or [])},
        "ledger": f["energyBalance"]["acuVsDeclaredRelativeError"],
        # Reported, not judged, where the ACU heat is not measurable (a box return).
        "serverLedger": ((f["energyBalance"]["serverHeatToAirW"] - f["energyBalance"]["declaredItPowerW"])
                         / f["energyBalance"]["declaredItPowerW"]
                         if f["energyBalance"].get("serverHeatToAirW") and f["energyBalance"].get("declaredItPowerW") else None),
        "cells": cells,
        "ranks": ranks,
        "solveSeconds": t[-1] if t else None,
        "secPerStep": (t[-1] - t[100]) / (len(t) - 101) if len(t) > 200 else None,
    }


def pct(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def compare(ref, var):
    acu = max((abs(var["acu"][k] - ref["acu"][k]) for k in ref["acu"]), default=0.0)
    common = sorted(set(ref["rack"]) & set(var["rack"]))
    d = [abs(var["rack"][k] - ref["rack"][k]) for k in common]
    checks = {
        "1 both CONVERGED": ref["verdict"] == "CONVERGED" and var["verdict"] == "CONVERGED",
        f"2 ACU return max diff {acu:.3f} K <= 0.5": acu <= 0.5,
    }
    if d:
        mae, p95, mx = sum(d) / len(d), pct(d, 0.95), max(d)
        checks[f"3 rack inlet MAE {mae:.3f} / p95 {p95:.3f} / max {mx:.3f} K (n={len(d)}) <= 0.5/1.0/2.0"] = \
            mae <= 0.5 and p95 <= 1.0 and mx <= 2.0
    if ref["ledger"] is not None and var["ledger"] is not None:
        checks[f"4 ledgers {ref['ledger']*100:+.3f}% / {var['ledger']*100:+.3f}% within 1%"] = \
            abs(ref["ledger"]) <= 0.01 and abs(var["ledger"]) <= 0.01
    elif ref["serverLedger"] is not None and var["serverLedger"] is not None:
        print(f"  (4 n/a: no ACU heat; server heat to air vs declared "
              f"{ref['serverLedger']*100:+.3f}% / {var['serverLedger']*100:+.3f}%, reported only)")
    return checks, len(common) == len(ref["rack"]) == len(var["rack"])


def main(argv):
    ref = load(argv[1])
    rows = [ref] + [load(p) for p in argv[2:]]
    print("| run | cells | ranks | iterations | s/step | core-us/cell/step | solve s |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        cost = r["secPerStep"] * r["ranks"] / r["cells"] * 1e6 if r["secPerStep"] else float("nan")
        print(f"| {r['name']} | {r['cells']:,} | {r['ranks']} | {r['iterations']} | "
              f"{r['secPerStep']:.2f} | {cost:.1f} | {r['solveSeconds']:.0f} |")
    for var in rows[1:]:
        checks, same = compare(ref, var)
        verdict = "PASS" if all(checks.values()) else "FAIL"
        print(f"\n{var['name']} vs {ref['name']}: {verdict}" + ("" if same else " (rack sets differ)"))
        for k, ok in checks.items():
            print(f"  [{'x' if ok else ' '}] {k}")


if __name__ == "__main__":
    main(sys.argv)
