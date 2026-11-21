#!/usr/bin/env python3
"""canon() and probe() must be seen to distinguish a factor of ten before they are trusted.

Three gates compare rendered PDF text against source through canon(): the page-content
gate, the shipped-PDF gate, and the measurement archive's rendering probe.  When canon()
reduced both sides to [a-z0-9] a reviewer replaced the tracked PDF with one whose abstract
said 16x10^-3 and 90x10^-3 instead of 1.6x10^-3 and 9.0x10^-3 -- a tenfold error in the
number that licenses "physically closed" -- and all three reported OK.

Fixing canon() closed half of it.  The other half was probe(), which pdf_content_gate.py
uses to pick the fragment of a caption or ledger row that it then looks for in the PDF: it
ran `re.sub(r"\$[^$]*\$", MARK, t)` FIRST, so every inline-math span was a cut point and
no number inside `$...$` could ever be part of a probe.  Ledger row (21) states the closure
floor as `$1.6\times10^{-3}$` and `$9.0\times10^{-3}$` and was probed on the prose around
them, so changing the row's `$1.19\times10^{-4}$` to `$11.9\times10^{-4}$` left the
rendered-PDF gate green -- verified, exit 0 -- even with canon() already keeping the decimal
point.  probe() now keeps the digits of a math span whose remaining TeX canon() deletes
anyway, and PROBE_POISONS below is the proof that it does.

These are the poisons that reproduce both, plus the controls that must NOT fire, because the
reason canon() folds punctuation at all is that pdftotext and LaTeX hyphenation otherwise
produce false failures.  A canon() that cries wolf gets deleted, and then nothing sees the
decimal point either.  The same argument applies to probe(): if it started keeping math that
renders as TEXT, every caption containing `\Jliq` would fail spuriously, so MATH_CONTROLS
pins the conservative half of the rule too.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_content_gate import canon, strip_tex, probe, math_numbers   # noqa: E402

POISONS = [
    ("1.6e-3 vs 16e-3", "median $1.6\\times10^{-3}$, worst $9.0\\times10^{-3}$",
     "median $16\\times10^{-3}$, worst $90\\times10^{-3}$"),
    ("1.45e-7 vs 1.45e-6", "matches central differences to $1.45\\times10^{-7}$",
     "matches central differences to $1.45\\times10^{-6}$"),
    ("0.0352 vs 0.352", "exact sign test $p = 0.0352$", "exact sign test $p = 0.352$"),
    ("7 of 8 vs 7 of 80", "$7$ of $8$ tasks", "$7$ of $80$ tasks"),
    ("311 vs 3110", "over the $311$ feasible designs", "over the $3110$ feasible designs"),
]

CONTROLS = [
    ("apostrophe folding", "the arm's own record", "the arm\u2019s own record"),
    ("quote folding", "``physically closed''", "\u201cphysically closed\u201d"),
    ("hyphenation at a line break", "the winner", "the win-ner"),
    ("en/em dash folding", "rows 29--32", "rows 29\u201332"),
    ("ligature folding", "five deficits", "\ufb01ve de\ufb01cits"),
    ("whitespace rewrapping", "the closure floor is\n  measured", "the closure floor is measured"),
    # canon() folds punctuation; stripping LaTeX markup is strip_tex()'s job, and the two
    # are composed by the callers.  Testing markup stripping through canon() alone was a
    # badly-specified control that failed for a reason that is not a defect -- which is
    # exactly how a real control gets deleted instead of fixed.
    ("no spurious folding of digits", "$7$ of $8$", "7 of 8"),
]


# probe() must be able to SEE a number inside inline math, i.e. the two probes must differ.
# Every pair is a real string from main.tex's ledger or captions, with one digit moved.
PROBE_POISONS = [
    ("ledger row 21's withdrawn single-seed floor, 1.19e-4 -> 11.9e-4",
     r"The closure floor was quoted as $1.19\times10^{-4}$ from three designs of one seed",
     r"The closure floor was quoted as $11.9\times10^{-4}$ from three designs of one seed"),
    ("ledger row 21's median closure floor, 1.6e-3 -> 16e-3",
     r"has median $1.6\times10^{-3}$ and maximum $9.0\times10^{-3}$, over the shipped",
     r"has median $16\times10^{-3}$ and maximum $9.0\times10^{-3}$, over the shipped"),
    ("a caption's plain count, $311$ -> $3110$",
     r"gives, on the $311$ designs whose hard solve both converged and served the load",
     r"gives, on the $3110$ designs whose hard solve both converged and served the load"),
    # Short prefix on purpose: probe() returns the first 40 canonical characters of the
    # longest run, so a number past character 40 is outside the probe no matter how
    # well probe() extracts it.  That is a property of the gate, not of this test.
    ("an exponent, 1.45e-7 -> 1.45e-6",
     r"per-block median of $1.45\times10^{-7}$ over the tasks",
     r"per-block median of $1.45\times10^{-6}$ over the tasks"),
]

# The conservative half: math that renders as TEXT the source does not contain must STILL be
# a cut point, or probe() produces false failures and gets deleted.
MATH_CONTROLS = [
    (r"$\Jliq + 133.7\,\Jair$", None),        # \Jliq renders as "Jliq"
    (r"$2.95\sigma$ over 238 rescues", None),   # a Greek macro is not in the whitelist
    (r"$1.6\times10^{-3}$", "1.6 10 3 "),       # and one that is
]


def main() -> int:
    fails = 0
    for name, a, b in POISONS:
        ca, cb = canon(a), canon(b)
        ok = ca != cb
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} poison {name}: "
              f"{'distinguished' if ok else f'COLLIDE as {ca!r}'}")
    for name, a, b in CONTROLS:
        ca, cb = canon(a), canon(b)
        ok = ca == cb
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} control {name}: "
              f"{'folded together' if ok else f'differ: {ca!r} vs {cb!r}'}")
    composed = canon(strip_tex(r"\textbf{bitwise} identical"))
    comp_ok = composed == canon("bitwise identical")
    fails += not comp_ok
    print(f"{'PASS' if comp_ok else 'FAIL'} strip_tex then canon folds markup: {composed!r}")
    # `probe_str`, not `probe`: a local of that name shadowed the imported function and
    # every probe poison below died with "'str' object is not callable".
    probe_str = canon("median $1.6\\times10^{-3}$, worst $9.0\\times10^{-3}$ over 311")[:24]
    long_enough = len(probe_str) == 24
    fails += not long_enough
    print(f"{'PASS' if long_enough else 'FAIL'} a 24-character probe is still available: "
          f"{probe_str!r}")
    for name, a, b in PROBE_POISONS:
        pa, pb = probe(a), probe(b)
        ok = bool(pa) and pa != pb
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} probe poison {name}: "
              f"{'distinguished' if ok else 'INVISIBLE to probe()'}\n"
              f"       {pa!r}\n       {pb!r}")
    for body, want in MATH_CONTROLS:
        got = math_numbers(body.strip("$"))
        ok = (got is None) if want is None else (got == want)
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} math control {body!r}: "
              f"{'transparent' if want else 'still a cut point'} -> {got!r}")
    # The bound check_measurement_archive.py needs.  It deletes \d{8,} out of the extracted
    # PDF text (the venue's line-number column lands there as one concatenated run) and then
    # looks for the stored probe in what is left, so a probe containing such a run can never
    # be found.  canon() glues digits across a \times it deletes, and transparent inline
    # math made that reachable: $2200\times1480$ canonicalises to "22001480".
    # Short prefix on purpose: probe() truncates to 40 canonical characters, so the digit
    # run has to fall inside the first 40 for the unbounded probe to carry it at all.
    src = (r"repeat captures differ at the $2200\times1480$ resolution by up to "
           r"eight channel levels, measured over two archives of the same scene")
    unbounded, bounded = probe(src), probe(src, max_digit_run=7)
    digit_run = re.compile(r"\d{8,}")
    ok = bool(digit_run.search(unbounded)) and not digit_run.search(bounded) \
        and len(bounded) >= 24
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} probe honours the caller's digit-run bound: "
          f"{unbounded!r} -> {bounded!r}")

    # The point of the whole change, stated once: the NUMBER is inside the probe, so the
    # fragment pdf_content_gate.py looks for in the rendered PDF carries it.
    a = r"quoted as $1.19\times10^{-4}$ from three designs of one seed and one arm only"
    b = a.replace("1.19", "11.9")
    composed = "1.19104" in probe(a) and "11.9104" in probe(b) and probe(a) != probe(b)
    fails += not composed
    print(f"{'PASS' if composed else 'FAIL'} the number is inside the probe: "
          f"{probe(a)!r} vs {probe(b)!r}")

    print("\nCANON SELF-TEST " + ("OK" if not fails else f"FAILED ({fails})"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
