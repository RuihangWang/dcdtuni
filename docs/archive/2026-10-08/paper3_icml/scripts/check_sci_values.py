#!/usr/bin/env python3
"""Every scientific-notation number in the manuscript must exist in a shipped record.

Why this gate exists.  The claim gates (measure_e10/e12/e13/e14/e15/e11e/costs) anchor each
number to the SENTENCE that states it, which is what makes them immune to a digit appearing
elsewhere by coincidence.  The cost is that a number stated in two places is only checked in one:
a round-6 review shifted nine numbers in the abstract and body by a factor of ten -- the closure
floor 1.6e-3 to 1.6e-2, the adjoint accuracy 1.45e-7 to 1.45e-6, the cost ratio 5e3 to 5e4, the
placement tie 1.9e-15 to 1.9e-5 -- and NINE OF NINE left every gate green, because each shifted
site was a second statement of a number whose anchored site was untouched.

This gate attacks the same defect from the other direction.  It does not care which sentence a
number is in: it takes every `M x 10^E` in the document, computes the value, and requires that
value to appear in some tracked record within a relative tolerance.  A decimal shift moves the
value off every record and fails, wherever in the document it happens.

Scientific notation is the right scope for a first pass because it is where the paper's
load-bearing measurements live, because a shift there is always a change of VALUE rather than of
rounding, and because there are few enough of them that every exemption can carry a reason.
Plain decimals are policed by the anchored gates and by check_no_number_lost.py.

Exemptions are for named constants that are not measurements -- the 1e9 infeasibility penalty, the
1e-9 solver tolerance, page and coordinate counts -- and each must state what it is.  An
exemption with no reason is the defect class this repository keeps rediscovering.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RTOL_FLOOR = 0.005   # a lower bound, so a one-digit mantissa is not held to absurd precision


def printed_rtol(mant: str) -> float:
    """Half a unit in the last printed digit, as a relative tolerance.

    A correctly rounded rendering of a record value must lie inside this.  Using a flat tolerance
    instead rejects the manuscript's own numbers -- "1.9e-15" is the correct two-significant-figure
    rendering of the record's 1.94025e-15, which is 2.1% away and so failed a 2% test.
    """
    digits = mant.lstrip("-+")
    dec = len(digits.split(".")[1]) if "." in digits else 0
    unit = 0.5 * 10.0 ** (-dec)
    m = abs(float(digits))
    return max(unit / m if m else 1.0, RTOL_FLOOR)

# Two kinds of number legitimately have no record behind them.
#
# (1) A BARE power of ten -- `$10^{-9}$`, `$10^9$` -- which is either a named constant in the code
#     or the order-of-magnitude form of a quantity whose VALUE is gated by one of the measure_*
#     scripts.  Exempting the order-of-magnitude form is safe precisely because the mantissa is 1:
#     there is no digit to shift.
BARE_EXPONENTS = {
    9: "SOLVER_FAIL_J, the infeasibility penalty: a constant in the code, not a measurement",
    6: "the threshold above which no arm logged a rescale (A11.6 / Amendment 29)",
    3: "order-of-magnitude form of the cost ratio; the value 4834 is gated by measure_costs.py",
    -3: "order-of-magnitude form of the closure floor; the value is gated by measure_e14.py",
    -4: "order-of-magnitude form of the single-seed closure gap; gated by measure_e10.py",
    -6: "E10's finite-difference gate threshold fd_tol, a constant in the code",
    -7: "order-of-magnitude form of the adjoint accuracy; the value is gated by measure_e10.py",
    -9: "the damped-Newton convergence tolerance in softlc.py, a constant in the code",
    11: "the rescale-saturation threshold V0 tests, from A11.6",
    12: "the float32 saturation signature A11.6 pre-registered",
    -13: "appears only inside the quoted withdrawal of the false 'relative 1.3e-13' claim",
    -14: "the order of a float64 summation-order difference, not a measurement",
    -15: "the order of a float64 machine-precision tie, not a measurement",
    -16: "the order of a float64 relative epsilon, not a measurement",
}

# (2) A SPECIFIC value that is derived arithmetic or a quoted withdrawal.  Keyed exactly, so a
#     neighbouring value is not exempted by proximity.
EXEMPT_VALUES = {
    (3.1e7, 7): ("derived arithmetic, not a measurement: SOLVER_FAIL_J 1e9 divided by 32 samples "
                "per step, the group mean a single penalised sample would have produced", None),
    (1.3e-13, -13): ("quoted inside the withdrawal of the false 'one ULP (relative 1.3e-13)' claim "
                    "in app:derivations; the ledger's copy is excluded by section, this one is not, "
                    "and a withdrawn number has to stay visible to stay withdrawn", None),
    # Two entries that used to sit here were DELETED, by the audit that now requires
    # every exemption to fire on the unpoisoned document.  (1.6e-2, -2) was written
    # for "a PREDICTED floor in the E11d D0 comparison", but no such value is in the
    # document: the sentence it was named after prints $-1.64\times10^{-2}$, whose
    # mantissa is outside the key's tolerance.  The only string that ever matched it
    # was $1.6\times10^{-2}$ -- which is what the closure floor's median becomes
    # under a tenfold shift, so the entry was not an exemption for a predicted floor
    # but a licence to shift a measured one, exactly as round 7 predicted.
    # (6.18e-4, -4) had the wrong sign: the document prints $-6.18\times10^{-4}$ and
    # that value is matched by a record, so the entry never fired either.
    # Derived by a committed script on every run rather than stored: the record holds the two
    # arms' objectives, not their ratio.  The exemption is keyed to the exact value, so shifting
    # the printed number by any factor breaks the exemption and fails the gate -- which is the
    # whole point of keying exemptions exactly instead of by proximity.
    (1.24e-3, -3): ("the worst relaxed S-versus-D gap, computed by scripts/measure_e14.py from "
                   "records/e14_search.json and printed by that gate on every run", None),
    (3.4445e-1, -1): ("the distance from the median replicate-averaged S-R margin for tasks 900050 "
                     "and 900054, derived from E11b's per-task margins in the campaign snapshot; "
                     "the tie the figure caption discloses is between two computed distances, so "
                     "no record stores the value", None),
}



# Sources that hold bookkeeping rather than measurements.  Round 7 found this gate
# certifying a prose-carried physics number ($2.3\times10^4$, E12d's normaliser)
# against `manifest.json`'s recorded FILE LENGTH of 23299 bytes and against another
# arm's `J_excess_median`.  A byte count is a number; it is not a measurement, and
# a gate that accepts it as one has certified nothing.
DENY_FILES = ("campaign_snapshot/manifest.json",)
DENY_KEY = re.compile(
    r"(^|\.)(sha|hash|bytes|len|length|size|n_bytes|old_revision|repo_head|"
    r"n_lines|line_count|revision)(\.|\[|$)", re.I)


def is_denied(fname: str, jpath: str) -> bool:
    return any(d in fname for d in DENY_FILES) or bool(DENY_KEY.search(jpath or ""))

def record_values(root: Path):
    """Every numeric leaf in every tracked record, with the file it came from."""
    out = []

    def walk(d, path, fname):
        if isinstance(d, dict):
            for k, v in d.items():
                walk(v, f"{path}.{k}", fname)
        elif isinstance(d, list):
            for i, v in enumerate(d):
                walk(v, f"{path}[{i}]", fname)
        elif isinstance(d, bool):
            return
        elif isinstance(d, (int, float)):
            out.append((float(d), path, fname))
    for f in sorted(root.glob("*.json")):
        try:
            walk(json.loads(f.read_text()), "", f.name)
        except Exception as e:                      # noqa: BLE001
            print(f"  warn: could not read {f.name}: {e}", file=sys.stderr)
    for f in sorted((root / "campaign_snapshot").glob("*.json")):
        try:
            walk(json.loads(f.read_text()), "", f"campaign_snapshot/{f.name}")
        except Exception:
            pass
    # Committed records that live beside the case that produced them.  A number backed by one of
    # these is not prose-carried, and reporting it as unmatched would train the reader to ignore
    # this gate.
    # Walk up until the repository root is found rather than assuming a fixed depth: a fixed
    # parents[3] raised IndexError when this gate ran from a scratch copy of the paper directory,
    # and the resulting traceback exited 1 -- which a poison test reads as "the gate caught it"
    # when in fact the gate crashed.  A poison that passes for the wrong reason is worse than no
    # poison, because it is the one that gets believed.
    repo = root
    for _ in range(6):
        if (repo / "ddl" / "case").is_dir():
            break
        if repo.parent == repo:
            break
        repo = repo.parent
    for f in sorted((repo / "ddl" / "case").glob("**/tracked/*.json")) if (repo / "ddl" / "case").is_dir() else []:
        try:
            walk(json.loads(f.read_text()), "", str(f.relative_to(repo)))
        except Exception:
            pass
    return out


SCI = re.compile(r"\$?\s*(-?[0-9]*\.?[0-9]+)\s*\\times\s*10\^\{?\s*(-?[0-9]+)\s*\}?\s*\$?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    ap.add_argument("--records", default=str(HERE / "records"))
    ap.add_argument("--list-exempt", action="store_true")
    ap.add_argument("--report-matches", action="store_true",
                    help="print the record path that certified each accepted value, so a "
                         "match against an unrelated leaf is visible instead of silent")
    args = ap.parse_args()

    tex = Path(args.tex).read_text()
    # The ledger and the withdrawn-claims appendix quote false numbers ON PURPOSE, to show what
    # was retracted.  Those are the one place a value with no record is correct, so they are
    # excluded by section rather than exempted one by one -- and the exclusion is reported.
    cut = tex.find(r"\section{The defect ledger}")
    quoted = tex[cut:] if cut > 0 else ""
    body = tex[:cut] if cut > 0 else tex

    vals = record_values(Path(args.records))
    if not vals:
        print("FAIL: no numeric values found in any tracked record, so nothing can be checked. "
              "A gate with an empty denominator must fail, not pass.", file=sys.stderr)
        return 2
    allowed = [(v, p, f) for v, p, f in vals if v != 0 and not is_denied(f, p)]
    denied = [(v, p, f) for v, p, f in vals if v != 0 and is_denied(f, p)]
    numbers = [v for v, _, _ in allowed]
    if denied:
        print(f"  sci-values: {len(denied)} numeric leaf/leaves excluded as bookkeeping "
              f"({', '.join(sorted({f for _, _, f in denied})[:3])} and keys matching "
              f"{DENY_KEY.pattern[:48]}...); a byte count may not certify a measurement")

    problems, checked, exempt_hits, unmatched = [], 0, [], []
    matched_by = []
    fired_exemptions = set()
    for m in SCI.finditer(body):
        mant, exp = m.group(1), int(m.group(2))
        value = float(mant) * 10.0 ** exp
        if value == 0:
            continue
        ctx = body[max(0, m.start() - 70):m.end() + 30].replace("\n", " ")
        hit = next(((k, why, cx) for k, (why, cx) in EXEMPT_VALUES.items()
                    if k[1] == exp and abs(value - k[0]) <= printed_rtol(mant) * abs(k[0])
                    and (cx is None or re.search(cx, ctx, re.I))), None)
        if hit is not None:
            fired_exemptions.add(hit[0])
            exempt_hits.append((f"{mant}e{exp}", hit[1][:70]))
            continue
        if float(mant) == 1.0 and exp in BARE_EXPONENTS:
            exempt_hits.append((f"{mant}e{exp}", BARE_EXPONENTS[exp][:70]))
            continue
        best = min(numbers, key=lambda v: abs(value - v) / max(abs(v), 1e-300))
        rel = abs(value - best) / max(abs(best), 1e-300)
        if rel <= printed_rtol(mant):
            checked += 1
            src = next((f"{f} {p}" for v, p, f in allowed if v == best), "?")
            matched_by.append((f"{mant}e{exp}", src, rel))
        else:
            src = next((f"{f} {p}" for v, p, f in allowed if v == best), "?")
            # name a closer-but-denied source, because "no record holds it" is less
            # useful than "the only thing within tolerance is a byte count"
            dnear = None
            if denied:
                dv = min(denied, key=lambda t: abs(value - t[0]) / max(abs(t[0]), 1e-300))
                drel = abs(value - dv[0]) / max(abs(dv[0]), 1e-300)
                if drel <= printed_rtol(mant):
                    dnear = f"{dv[0]:.6g} in {dv[2]} {dv[1]} ({drel * 100:.1f}% away), " \
                            "which is bookkeeping and may not certify a measurement"
            unmatched.append((mant, exp, value, best, rel, src, ctx.strip(), dnear))

    # An exemption that never fires is dead weight, and dead weight in an exemption
    # list is how a bare-token licence survived six rounds elsewhere in this suite.
    dead = [k for k in EXEMPT_VALUES if k not in fired_exemptions]
    for k in sorted(dead):
        problems.append(
            f"the exemption for {k[0]:.6g} (10^{k[1]}) never fired on this document, so it is "
            f"either stale or its context pattern no longer matches the sentence it was written "
            f"for; either way it is an unexercised licence and must be removed or repaired. "
            f"Reason on file: {EXEMPT_VALUES[k][0][:110]}")

    for mant, exp, value, best, rel, src, ctx, dnear in unmatched:
        problems.append(
            f"{mant}x10^{exp} = {value:.6g} appears in the manuscript but no tracked record holds "
            f"it; the nearest value is {best:.6g} in {src}, {rel * 100:.1f}% away against a "
            f"{printed_rtol(mant) * 100:.1f}% printed-precision tolerance. "
            + (f"The only source within tolerance is {dnear}. " if dnear else "")
            + f"Either the number is prose-carried or it has been shifted. "
              f"Context: ...{ctx[-90:]}...")

    if args.list_exempt:
        for e, why in sorted(BARE_EXPONENTS.items()):
            print(f"  exempt bare 10^{e}: {why}")
        for (v, e), (why, cx) in sorted(EXEMPT_VALUES.items()):
            print(f"  exempt {v:.6g} (10^{e})" + (f" [context /{cx}/]" if cx else "") + f": {why}")
    if args.report_matches:
        for tok, src, rel in matched_by:
            print(f"  certified {tok:>12s} by {src}  ({rel * 100:.2f}% away)")

    print(f"  sci-values: {checked} matched a tracked record, {len(exempt_hits)} are named "
          f"constants, {len(unmatched)} unmatched; {len(numbers)} record values scanned; the "
          f"ledger section is excluded because it quotes withdrawn numbers deliberately")
    if problems:
        print("SCI-VALUE GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print("sci-value gate OK (every scientific-notation value in the body exists in a record "
          "or is a named constant)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
