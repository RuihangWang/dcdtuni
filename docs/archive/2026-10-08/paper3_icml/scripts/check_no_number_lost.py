#!/usr/bin/env python3
"""No number may leave the document.

Compression to a page limit is the operation that silently deleted seventeen measurements
last revision while the build stayed green, and the fix that round was a review diffing two
revisions by hand.  This is that diff as a script: it extracts every numeric token from
main.tex and gen_appendix.tex at two revisions and reports the ones that were there and are
not now.

It is a *loss* check, not an equality check, and it distinguishes two things that a plain
multiset difference conflates.  A number that leaves the DOCUMENT is a lost measurement.  A
number that occurs fewer times because a paragraph duplicated between the body and an
appendix was compressed into one is a different event: the body has a page limit and the
appendix does not, so moving a figure there, and dropping the body's second copy of it, is
the intended operation.  BOTH are failures unless exempted, because both are a quantity
getting thinner, and the only thing that separates them is the reason recorded against the
exemption -- so each is permitted individually with --allow and a reason, and the reason is
printed with the finding.  (This docstring used to say "only the first is a failure" while
the code failed on `fewer` too, which is how the second kind acquired a reputation for
being advisory and got ignored.)

Every exemption carries the EXACT number of occurrences it permits to be lost, per
baseline.  A loss above the bound fails, and so does a loss below it, because a bound
that is not used is a licence waiting for the next edit.  Exempting a bare token with
no count is what let `$4\times$` -- the paper's only positive measurement -- be changed
to `$5\times$` and committed through a fully green suite, since the token `4` occurs 45
times and any one of them could leave.

TWO baselines are compared, and this is the part that was missing.  The pinned baseline in
`records/compression_baseline.json` is the revision before the last compression, which is
the right thing to compare a compression against and the wrong thing to compare an edit
against: at HEAD, 53 of 409 distinct numeric tokens stood ABOVE their baseline count, so
each of those 53 could be decremented -- once, or fifty times -- without the gate moving.
Changing the abstract's `$1.6\\times10^{-3}$` to `$1.6\\times10^{-4}$`, a tenfold error in
the number that licenses "physically closed", took token `3` from 60 to 59 against a
baseline of 50 and the gate printed NO-NUMBER-LOST OK.  So the working tree is now compared
against HEAD as well, and no token may drop relative to the last committed state without a
recorded reason.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
FILES = ("docs/manuscripts/paper3_icml/main.tex",)
# NOT gen_appendix.tex.  It is generated from record JSONs, so its numbers move whenever a
# campaign lands another task, and comparing it makes every data arrival look like a
# deleted measurement.  It also cannot be hand-compressed: check_appendix_fresh.py fails on
# any difference from what the records generate now.
# a numeric token, with the LaTeX that gives it meaning: 1.45\times10^{-7}, 66.8$\times$,
# $p = 0.0078$, 910034, $7$ of $7$.  Bare digits are kept too, and the comparison is on the
# digit strings, because what must not vanish is the quantity, not its formatting.
NUM = re.compile(r"\d+(?:\.\d+)?")


def read(rev: str | None) -> str:
    if rev is None:
        return "".join((REPO / f).read_text(encoding="utf-8") for f in FILES)
    out = []
    for f in FILES:
        r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{f}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"cannot read {rev}:{f}: {r.stderr.strip()[:200]}")
        out.append(r.stdout)
    return "".join(out)


def tokens(src: str) -> Counter:
    # strip comments so a commented-out number is not counted as present
    src = re.sub(r"(?<!\\)%.*", "", src)
    return Counter(NUM.findall(src))


def norm_allows(allows: dict, kind: str):
    """(bound, reason) per token for one baseline.  A missing bound is ZERO, not
    unlimited, and a bare-string exemption is refused: it is the form that let the
    paper's only positive measurement be edited through a green suite."""
    out = {}
    for k, v in allows.items():
        if isinstance(v, str):
            raise SystemExit(
                f"exemption for {k!r} is a bare reason string with no bound.  A token-keyed "
                f"exemption with no count is a document-wide licence to lose that token "
                f"anywhere in the file, which is how `$4\\times$` became `$5\\times$` "
                f"through a fully green suite.  Use {{'reason': ..., 'pinned': N, 'head': M}}.")
        if not isinstance(v, dict) or "reason" not in v:
            raise SystemExit(f"exemption for {k!r} must be a dict carrying a 'reason': {v!r}")
        b = v.get(kind)
        if b is not None and not isinstance(b, int):
            raise SystemExit(f"exemption for {k!r} has a non-integer {kind} bound {b!r}")
        out[k] = (b, v["reason"], v)
    return out


def compare(b_src: str, c_src: str, allows: dict, label: str, identical_is_failure: bool,
            kind: str = "pinned"):
    """One before/after comparison.  Returns a list of unexplained findings.

    Everything is printed as it is found, so a reader sees the same table for both
    baselines; the return value is only the part that fails.
    """
    allows = norm_allows(allows, kind)
    print(f"\n== {label}")
    if b_src == c_src:
        if identical_is_failure:
            print("NO-NUMBER-LOST FAILED: before and after are the same text, so nothing "
                  "was compared and a check with nothing to check is the defect it exists "
                  "to catch", file=sys.stderr)
            return ["before and after are the same text, so nothing was compared"]
        print("  identical to the working tree: nothing has changed since that revision, "
              "so there is nothing to lose")
        return []

    b, c = tokens(b_src), tokens(c_src)
    # A number that leaves the DOCUMENT is a lost measurement.  A number that occurs once
    # less because a duplicated paragraph was compressed into one is a thinned one.  Both
    # are reported and both fail without a recorded reason; only the reason differs.
    gone = {k: b[k] for k in b if c.get(k, 0) == 0}
    fewer = {k: b[k] - c[k] for k in b if 0 < c.get(k, 0) < b[k]}
    gained = {k: c[k] - b.get(k, 0) for k in c if c[k] > b.get(k, 0)}
    # total loss per token, which is what a bound is stated against: a token that
    # left entirely and one that merely thinned are the same kind of event here.
    loss = {k: b[k] - c.get(k, 0) for k in b if c.get(k, 0) < b[k]}
    allowed = {k for k, (bnd, _, _) in allows.items() if bnd is None or bnd > 0}
    guards = {k for k, (bnd, _, _) in allows.items() if bnd == 0}

    unexplained, unexplained_fewer, over, under = {}, {}, {}, {}
    for k, v in loss.items():
        if k not in allows:
            (unexplained if c.get(k, 0) == 0 else unexplained_fewer)[k] = v
            continue
        bnd = allows[k][0]
        if bnd is None:
            print(f"    {'UNBOUND':8s} {k:>12s}  -{v} permitted without a count by an "
                  f"explicit --allow on this invocation only: {allows[k][1][:80]}")
        elif v > bnd:
            over[k] = (v, bnd)
        elif v < bnd and kind == "pinned":
            # Exact only against the pinned baseline, which is a fixed revision whose
            # losses are known constants.  HEAD moves, so a bound there is a maximum:
            # demanding equality would make every exemption stale the moment the edit
            # that needed it was committed.
            under[k] = (v, bnd)
    print(f"  {sum(b.values())} numeric tokens before, {sum(c.values())} after; "
          f"{len(gone)} distinct numbers left the document entirely, {len(fewer)} occur "
          f"fewer times, {len(gained)} arrived")
    for k in sorted(gone, key=lambda x: (-gone[x], x)):
        print(f"    {'allowed' if k in allowed else 'GONE':8s} {k:>12s}  was x{gone[k]}"
              + (f"  reason: {allows[k][1][:90]}" if k in allowed else ""))
    for k in sorted(fewer, key=lambda x: (-fewer[x], x)):
        tag = "allowed" if k in allowed else "FEWER"
        print(f"    {tag:8s} {k:>12s}  -{fewer[k]} (still present x{c[k]})"
              + (f"  reason: {allows[k][1][:90]}" if k in allowed else ""))
    for k in sorted(over):
        v, bnd = over[k]
        print(f"    {'OVER':8s} {k:>12s}  lost {v} occurrence(s) but the exemption on this "
              f"baseline permits exactly {bnd}: {allows[k][1][:90]}", file=sys.stderr)
    for k in sorted(under):
        v, bnd = under[k]
        print(f"    {'STALE':8s} {k:>12s}  the exemption permits losing {bnd} but only {v} "
              f"was lost, so the bound is stale and a stale bound is a licence waiting for "
              f"the next edit: {allows[k][1][:90]}", file=sys.stderr)
    for k in sorted(guards):
        if k not in loss:
            print(f"    {'GUARD':8s} {k:>12s}  bound 0 on this baseline: any future loss of "
                  f"this token fails.  {allows[k][1][:80]}")
    if unexplained or unexplained_fewer or over or under:
        out = []
        for k in sorted(unexplained):
            out.append(f"[{label}] gone: {k} was x{gone[k]}")
        for k in sorted(unexplained_fewer):
            out.append(f"[{label}] fewer: {k} -{unexplained_fewer[k]}, still x{c[k]}")
        for k in sorted(over):
            out.append(f"[{label}] over-bound: {k} lost {over[k][0]} but the exemption permits "
                       f"{over[k][1]}")
        for k in sorted(under):
            out.append(f"[{label}] stale bound: {k} lost {under[k][0]} but the exemption permits "
                       f"exactly {under[k][1]} -- narrow it or remove it, because a bound that "
                       f"is not used is a licence waiting for the next edit")
        print(f"  NO-NUMBER-LOST FAILED on this baseline: {len(unexplained)} numbers left "
              f"the document entirely, {len(unexplained_fewer)} occur fewer times, "
              f"{len(over)} exceeded their bound and {len(under)} came in under it",
              file=sys.stderr)
        for x in out:
            print(f"    - {x}", file=sys.stderr)
        return out
    print(f"  NO-NUMBER-LOST OK on this baseline (every loss matched its bound exactly; "
          f"{len(allowed)} bounded exemption(s), {len(guards)} zero-bound guard(s))")
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", nargs="?", default=None,
                    help="git revision, or '-' for the working tree; omit when --config "
                         "carries the baseline")
    ap.add_argument("--after", default="-", help="git revision, or '-' for the working tree")
    ap.add_argument("--allow", action="append", default=[],
                    help="NUM=REASON, repeatable: a number permitted to leave or to occur "
                         "fewer times, with the reason recorded in the output")
    ap.add_argument("--config", default=None,
                    help="tracked JSON carrying `before` and `allows`, so the baseline is "
                         "part of the repository rather than part of someone's command line")
    ap.add_argument("--no-head-comparison", action="store_true",
                    help="skip the second baseline (HEAD).  Only for an explicit "
                         "before/after invocation; the working-tree check always runs both, "
                         "because the pinned baseline alone lets any token that is above it "
                         "be decremented silently")
    a = ap.parse_args()

    allows = {}
    for spec in a.allow:
        if "=" not in spec:
            raise SystemExit(f"--allow takes NUM=REASON, got {spec!r}: an exemption with no "
                             "reason is the defect this gate's own docstring promised it "
                             "would not have, and it leaves no artifact")
        k, v = spec.split("=", 1)
        # None bounds mean "permitted without a count, on this invocation only".
        # A committed exemption in --config must carry integers.
        allows[k.strip()] = {"reason": v.strip(), "pinned": None, "head": None}
    before = a.before
    if not before and not a.config:
        raise SystemExit("no baseline: give a revision, '-' for the working tree, or "
                         "--config pointing at records/compression_baseline.json")
    if a.config:
        cfg = json.loads(Path(a.config).read_text())
        before = cfg["before"]
        for k, v in (cfg.get("allows") or {}).items():
            allows.setdefault(str(k), v)
        legacy = sorted(k for k, v in allows.items() if isinstance(v, str))
        if legacy:
            raise SystemExit(
                f"{a.config}: {len(legacy)} exemption(s) are bare reason strings with no bound "
                f"({', '.join(legacy)}).  A token-keyed exemption with no count permits that "
                f"token to be lost anywhere in the document; give each an exact "
                f"'pinned' and 'head' count.")
        print(f"baseline {before} from {a.config}: {cfg.get('reason', '(no reason given)')}")
    a.before = before
    if a.before == a.after:
        raise SystemExit(f"before and after are both {a.before!r}, so nothing would be "
                         "compared; advance the baseline in the commit that compresses")

    b_src = read(None if a.before == "-" else a.before)
    c_src = read(None if a.after == "-" else a.after)
    fails = compare(b_src, c_src, allows, f"pinned baseline {a.before}", True,
                    kind="pinned")

    # The second baseline.  HEAD is compared only when `after` is the working tree, which
    # is the case check_manuscript.py runs: comparing two committed revisions against HEAD
    # as well would be a third thing nobody asked for.
    head_rev = None
    if a.after == "-" and not a.no_head_comparison:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fails.append(f"cannot resolve HEAD ({r.stderr.strip()[:120]}), so the "
                         "second baseline -- the one that stops a token above its pinned "
                         "baseline being decremented silently -- could not be compared")
        else:
            head_rev = r.stdout.strip()
            fails += compare(read(head_rev), c_src, allows,
                             f"HEAD {head_rev} (the last committed state)", False,
                             kind="head")

    if fails:
        print(f"\nNO-NUMBER-LOST FAILED: {len(fails)} unexplained loss(es)", file=sys.stderr)
        return 1
    n = "two baselines" if head_rev else "one baseline"
    print(f"\nNO-NUMBER-LOST OK ({n}: the pinned baseline"
          + (f" and HEAD {head_rev}" if head_rev else "")
          + "; every number that left or thinned was accounted for)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
