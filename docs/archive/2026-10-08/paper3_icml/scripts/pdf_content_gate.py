#!/usr/bin/env python3
"""Assert that the BUILT PDF contains what the source promises.

Every other gate in this repository checks a number against a record, and the records
are generated at build time, so the chain from disk to .tex is closed.  Nothing closed
the last step.  A `table*` holding all twenty-seven ledger rows is 1191.74 pt tall;
LaTeX typesets what fits and silently drops the rest, so the shipped PDF rendered rows
1-16 with NO CAPTION while four places in the prose said "all twenty-seven" and pointed
at that table.  The build was green: 0 errors, 0 overfull boxes, 0 undefined references.
Row (27) -- the defect about an appendix contradicting the records on disk -- was itself
invisible, which is the same failure in a new organ.

So this checks the rendered artifact rather than the source:
  * every float's caption must appear in the extracted PDF text;
  * every numbered row of the defect ledger must appear;
  * a count promised in prose ("all twenty-seven") must match the rows actually found.

Matching is on whitespace-normalised text with LaTeX markup stripped, because pdftotext
re-wraps lines and ligatures differ.  A row is identified by its first few words, which
are distinctive by construction; the check fails if the fragment is absent, not if the
row moved.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


# Digits and DECIMAL POINTS survive; everything else is folded away.  Keeping "." is
# what makes this gate able to see a 10x error at all -- see the module docstring
# of canon() and test_canon.py, which poisons it.
# \x00 is the placeholder canon() puts where a real decimal point was; the fold has to
# let it through or the decimal point is dropped before it can be restored.
_CANON_DROP = re.compile(r"[^a-z0-9\x00]+")
# A dot carries a factor of ten only between two digits.  Protecting just that dot is what
# lets this gate see 16e-3 where the source says 1.6e-3, without making it sensitive to
# \ldots, to a sentence-final period, or to the three dots pdftotext emits for an ellipsis.
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_DECIMAL_MARK = "\x00"


def canon(t: str) -> str:
    """Canonical form for comparison: ASCII-folded, lowercase, alphanumerics only.

    Comparing rendered text against source text has to survive three independent
    rewrites -- LaTeX markup, pdftotext's line re-wrapping and ligature/quotation
    substitution (it emits U+2019 for a source apostrophe and U+201C for ``...\'),
    and hyphenation at line breaks.  A first version of this gate matched on
    whitespace-normalised text with markup stripped and reported nine of twenty-seven
    ledger rows as missing when only eleven actually were: the false failures were
    apostrophes.  Folding punctuation away removes the whole class, and a probe of 24
    canonical characters is still long enough to be distinctive.

    But [a-z0-9] alone also deleted the DECIMAL POINT, which made this gate -- and the
    shipped-PDF gate and the archive's rendering probe, which all compare through here --
    blind to a factor of ten: "1.6e-3" and "16e-3" canonicalised identically, and a
    reviewer replaced the tracked PDF with one saying the latter and all three reported OK.
    Digits and decimal points are therefore kept.  Hyphens are still dropped, because
    LaTeX hyphenates words across lines and pdftotext shows the break ("win-ner"), and
    apostrophes are still dropped, because that is what the nine false failures were.
    """
    t = (t.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"')
          .replace("\u201d", '"').replace("\u2013", "-").replace("\u2014", "-")
          .replace("\u2212", "-").replace("\ufb01", "fi").replace("\ufb02", "fl")
          .replace("\u00a0", " "))
    t = t.lower()
    t = _DECIMAL.sub(_DECIMAL_MARK, t)
    return _CANON_DROP.sub("", t).replace(_DECIMAL_MARK, ".")


def strip_tex(t: str) -> str:
    # \ldots and \dots are TEXT, not markup to be discarded: pdftotext emits three dots
    # for them, and canon() keeps dots so that it can see a decimal point.  Mapping them
    # here is what keeps the two sides of every probe comparison equal.
    t = t.replace(r"\ldots", "...").replace(r"\dots", "...")
    t = re.sub(r"\\[a-zA-Z]+\*?", " ", t)          # commands
    t = re.sub(r"[{}$&~_^\\%]", " ", t)             # grouping and escapes
    t = t.replace("--", "-").replace("''", '"').replace("``", '"')
    return re.sub(r"\s+", " ", t).strip()


MARK = "\x00"

# Inline math, split so its NUMBERS can be compared instead of deleted.
#
# probe() used to run `re.sub(r"\$[^$]*\$", MARK, t)`: every inline-math span became a cut
# point, so a caption or ledger row was probed only on the prose BETWEEN its numbers.  The
# consequence was structural rather than incidental -- no number inside `$...$` was visible
# to this gate by construction, and canon()'s whole reason for keeping the decimal point
# (see its docstring and test_canon.py) never reached a caption or a row.  Ledger row (21)
# states the closure floor as `$1.6\times10^{-3}$` and `$9.0\times10^{-3}$` and was probed
# on the words around them, so a PDF saying `16` and `90` left this gate green.
#
# Not all math can be kept.  A span like `$\Jliq + 133.7\,\Jair$` renders as
# "Jtot = Jliq + 133.7 Jair", i.e. as TEXT the source does not contain, and guessing at that
# is how the false failures in probe()'s docstring arrived.  So the rule is conservative and
# one-directional: a span is transparent only if, once the symbol macros canon() deletes
# anyway are removed, nothing but digits and ASCII punctuation is left.  A span carrying a
# letter or an unknown macro stays a cut point, exactly as before.
_MATH_SYMBOL = re.compile(
    r"\\(?:times|cdot|div|pm|mp|leq?|geq?|neq?|approx|sim|simeq|propto|to|mid|"
    r"rightarrow|leftarrow|leftrightarrow|Rightarrow|Leftarrow|Leftrightarrow|iff|"
    r"infty|partial|nabla|ldots|dots|circ|star|bullet|dagger|perp|parallel|"
    r"in|notin|subset|supset|cup|cap|forall|exists|neg|land|lor|setminus|"
    r"alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|"
    r"kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|upsilon|"
    r"phi|varphi|chi|psi|omega|ell|hbar|Re|Im|"
    r"Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega|"
    r"quad|qquad|[,;!:> ])\*?")
_MATH_LEFTOVER = re.compile(r"^[\d\s.+\-,:;()<>=/^_{}|]*$")


def math_numbers(body: str):
    """The digits of one inline-math span, or None when the span is not transparent.

    `$1.6\\times10^{-3}$` gives `" 1.6 10 3 "`, whose canon() is `1.6103` -- exactly what
    pdftotext yields for the rendered `1.6 x 10-3`, because canon() keeps a decimal point
    between two digits and drops the multiplication sign and the superscript minus.
    `$\\Jliq + 133.7\\,\\Jair$` gives None and stays a cut point.
    """
    b = _MATH_SYMBOL.sub(" ", body).replace("~", " ")
    if not _MATH_LEFTOVER.match(b):
        return None
    return re.sub(r"[^\d.]+", " ", b)


def _cut_math(m):
    n = math_numbers(m.group(1))
    return f" {n} " if n is not None else MARK


# Truncation detectors.  The ledger grew to 32 rows and the captions to 11 when the five
# identical ledger captions were collapsed into one full and four short.
MIN_LEDGER_ROWS = 42
MIN_CAPTIONS = 11


# The longest digit run a probe may contain when its caller relaxes the PDF text by
# deleting long digit runs.  check_measurement_archive.py has to do that: this archive is
# flowing prose, an item can straddle a page break, and `pdftotext -raw` drops the venue's
# line-number column into the middle of the paragraph as one concatenated digit run.  A probe
# containing such a run can never be found in text the run was deleted from.
#
# probe() could not produce one before inline math became transparent, and can now: canon()
# glues digits across a `\times` it deletes, so `$2200\times1480$` canonicalises to
# "22001480".  Keeping the numbers is the whole point of the change, so the bound is the
# caller's to state rather than something probe() may assume.  pdf_content_gate.py itself
# passes none, because it probes captions and ledger rows, which live in floats that do not
# split mid-cell and need no relaxation.
PROBE_DIGIT_RUN_LIMIT = 7


def probe(t: str, want: int = 24, max_digit_run: int = 0) -> str:
    """The longest run of literal prose in a piece of source, canonicalised.

    A probe has to be a substring of what pdftotext extracts, and three things in the
    source do not survive as literal text: inline math renders as glyphs with spacing
    of its own ("$\\Jliq + 133.7\\,\\Jair$" extracts as "Jtot = Jliq + 133.7 Jair",
    so the surrounding words are no longer contiguous), \\ref resolves to a number the
    source does not contain ("\\S\\ref{sec:figures}" extracts as "6"), and ligatures and
    hyphenation differ.  So math and references are cut OUT rather than blanked, the
    caption or row is split at those cuts, and the probe is the longest remaining run.
    Taking the first 24 canonical characters of the whole string -- what a first version
    did -- produced false failures on one caption and three ledger rows, which is worse
    than no gate, because it trains whoever runs it to ignore the output.

    Inline math is no longer cut out wholesale.  `math_numbers()` keeps the digits of a
    span whose remaining TeX canon() would delete anyway, so a caption's
    `$1.6\times10^{-3}$` is now inside the probe and a tenfold error in it fails the gate.
    A span carrying a macro that renders as TEXT is still a cut point, which is the case
    the paragraph above is about.
    """
    t = re.sub(r"\$([^$]*)\$", _cut_math, t)                      # inline math
    t = re.sub(r"\\(?:ref|eqref|autoref|Cref|cref)\{[^}]*\}", MARK, t)
    t = re.sub(r"\\S(?![a-zA-Z])", MARK, t)
    t = re.sub(r"\\[a-zA-Z]+\*?", " ", t)                       # other commands
    t = re.sub(r"[{}~_^&%]", " ", t)
    runs = [canon(strip_tex(r)) for r in t.split(MARK)]
    # Longest first, then truncated: which window is "the longest run" has to be decided
    # before the truncation, not after it, or every run over 40 characters ties.
    cands = [r[:40] for r in sorted((r for r in runs if len(r) >= want),
                                    key=len, reverse=True)]
    if not cands:
        return ""
    if max_digit_run:
        bad = re.compile(r"\d{%d,}" % (max_digit_run + 1))
        # REJECT the window and take the next clean one.  A probe the caller deletes part of
        # can never be found in the text it searches, so returning it is not a fallback, it
        # is a probe that is guaranteed to fail -- and `build_archive.py` writes the result
        # into a TRACKED manifest, where it then fails a gate nobody can fix by rebuilding.
        clean = [c for c in cands if not bad.search(c)]
        if clean:
            return clean[0]
        # Every window carries such a run.  Cut at it and take the longest piece still long
        # enough to be distinctive, so the caller gets a usable probe rather than one it is
        # obliged to reject; if even that is impossible, return nothing and let the caller
        # say so.
        pieces = [p for c in cands for p in bad.split(c) if len(p) >= want]
        return max(pieces, key=len) if pieces else ""
    return cands[0]


def pdftext(pdf: Path) -> str:
    # -raw, not the default layout mode: a ledger table spans the full page width in
    # three columns, and layout mode reads it in page-columns, interleaving one cell's
    # text with its neighbour's.  Long rows then have no contiguous 24-character run
    # left to probe, and the gate reported five rows missing from a PDF whose ten
    # captions all rendered -- i.e. five false failures.  Raw order follows the
    # content stream, which for a tabular is cell by cell.
    out = subprocess.run(["pdftotext", "-raw", str(pdf), "-"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"pdftotext failed on {pdf}: {out.stderr[:400]}")
    return canon(strip_tex(out.stdout))


def fragments(source: str):
    """(float captions, ledger row starts) from main.tex."""
    caps = [probe(m) for m in
            re.findall(r"\\caption\{(.*?)\}\s*(?:\\label|\n)", source, re.S)]
    caps = [c for c in caps if c]
    # a ledger row is "<digits> & ... & ... \\" inside the defect tables
    rows = []
    for m in re.finditer(r"^(\d{1,2}) & (.+?) \\\\$", source, re.M):
        pr = probe(m.group(2))
        if pr:
            rows.append((int(m.group(1)), pr))
    return caps, rows


def main() -> int:
    here = Path(__file__).resolve().parents[1]
    src = (here / "main.tex").read_text(encoding="utf-8")
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "build" / "main.pdf"
    if not pdf.exists():
        print(f"PDF CONTENT GATE: cannot run, {pdf} missing", file=sys.stderr)
        return 2
    text = pdftext(pdf)
    caps, rows = fragments(src)

    problems = []
    n_cap_ok = 0
    for c in caps:
        pr = c
        if pr and pr in text:
            n_cap_ok += 1
        else:
            problems.append(f"a float caption is not in the built PDF: {pr!r}")

    seen = {}
    for num, frag in rows:
        pr = frag
        if pr and pr in text:
            seen[num] = True
        else:
            problems.append(f"ledger row {num} is not in the built PDF: {pr!r}")

    nums = sorted({n for n, _ in rows})
    if nums:
        missing = [n for n in nums if n not in seen]
        # The prose-promise comparison this module used to make here was DEAD, and a review
        # demonstrated it: its pattern `all (twenty-[a-z]+|\d{1,2}) (?:defects|rows)` matched
        # zero strings in main.tex, because the source reads "list all twenty-eight\nwith the
        # gate" -- the count and the noun are separated by a newline and by other words.  It
        # also mapped any "twenty-*" to `nums[-1]`, so it could never disagree with itself.
        # Inserting a lowercase "all twenty-seven defects" beside 28 rendered rows passed, and
        # deleting rows 26-28 from the source printed "25 ledger rows (25 found) ... OK" with
        # every prose claim of "twenty-eight" intact.  So the comparison moved to
        # check_prose_counts.py, which parses number words and hyphenated compounds properly,
        # counts the ledger rows itself, and fails when a pattern matches nothing.  What stays
        # here is the part only a rendered PDF can answer: that the rows and captions the
        # source promises are actually on the page.
        if missing:
            problems.append(f"ledger rows missing from the PDF: {missing}")

    # Fail on an empty denominator.  This gate passed on a main.tex whose caption
    # rewrite had eaten most of the document, reporting "1 captions (1 found), 0
    # ledger rows (0 found) ... OK" -- a check that treats "found nothing to check"
    # as "nothing wrong".  That is the fifth time this pattern has appeared in this
    # campaign (the page gate, the `and others` regex, the checkpointing guard (17),
    # the scorer's empty task records (22)), and each time the fix is the same: a
    # denominator of zero is a failure, not a pass.
    # Floors, raised each time the ledger grows.  They are a truncation detector, not a
    # count check -- check_prose_counts.py compares the count with the prose -- so a stale
    # floor is a weak gate rather than a wrong one, but it is stated as a constant here
    # rather than buried in a message that names a number the ledger no longer has.
    if len(nums) < MIN_LEDGER_ROWS:
        problems.append(f"only {len(nums)} numbered ledger rows parsed from main.tex; "
                        f"the ledger has had at least {MIN_LEDGER_ROWS} since row "
                        f"{MIN_LEDGER_ROWS} was added, so the row regex has stopped "
                        f"matching or the source is truncated")
    if len(caps) < MIN_CAPTIONS:
        problems.append(f"only {len(caps)} float captions parsed from main.tex; expected at "
                        f"least {MIN_CAPTIONS}")
    promised = max(nums) if nums else 0
    if promised and len(nums) != promised:
        problems.append(f"ledger rows are not contiguous 1..{promised}: missing "
                        f"{sorted(set(range(1, promised + 1)) - set(nums))}")

    print(f"PDF CONTENT GATE: {len(caps)} captions ({n_cap_ok} found), "
          f"{len(nums)} ledger rows ({len(seen)} found) in {pdf.name}")
    if problems:
        print("PDF CONTENT GATE FAILED:", file=sys.stderr)
        for x in problems:
            print(f"  - {x}", file=sys.stderr)
        return 1
    print("PDF CONTENT GATE OK (every caption and every ledger row is rendered)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
