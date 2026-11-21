#!/usr/bin/env python3
"""The claim-scoped numeric gates, poisoned.

A review found four fail-opens that all came from one design: the numeric gates tested
document-wide substring presence, so they certified that a digit string exists SOMEWHERE
rather than that any particular statement of it is correct.

  E14-2  the headline adjoint-versus-constant margin "5.07" was satisfied by an unrelated
         "-15.07" in a de-biasing table on another page, so the margin could be changed to
         any value and the gate stayed green;
  E14-1  "7$ of $7$" occurs six times, so falsifying the one in section 3 was invisible;
  E13-2  the tab:e13 caption's three hard-network rho values were prose-carried and
         unchecked -- 0.936 could become 0.500;
  E13-5b changing ONE of five occurrences of the informative-half rho left four correct
         copies to satisfy the gate.

Each poison below reproduces one, against a COPY of main.tex passed with --tex, so the
repository is never modified.

The anchors are REGEXES WITH THE VALUE IN A CAPTURE GROUP, and the poisoned value is
derived from whatever the document currently says by flipping its last digit.  They used
to be literals -- `median margin $-5.07\\%$`, `diameter $0.936$` -- which made this
self-test a second copy of the manuscript's current numbers: the day a campaign moved a
value, the anchor stopped matching and the self-test reported the drift as its own
failure, so the suite was red on a clean checkout for a reason that was not a defect in
any gate.  A poison must depend on the SHAPE of the claim, not on its value.

A poison that stops failing means the anchor drifted and the number is unchecked again,
which is why a drifting anchor is reported as a failure here rather than as a skip.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
TEX = HERE / "main.tex"

# (gate, name, anchored pattern with ONE numeric capture group, group index)
#
# Each pattern is the gate's own, copied here deliberately: if the gate's anchor moves and
# this one does not, the poison reports "anchor occurs 0 times" and the suite goes red,
# which is the intended failure.  `group` is the capture to falsify.
POISONS = [
    ("measure_e14.py", "E14-2 headline margin changed",
     r"S beats C .{0,90}?median margin \$(-?[\d.]+)\\%\$", 1),
    ("measure_e14.py", "E14-1 S-beats-C count falsified",
     r"S beats C .{0,20}?\\textbf\{\$(\d+)\$ of \$(\d+)\$\}", 1),
    ("measure_e14.py", "S-beats-R assignment count falsified",
     r"S beats R .{0,20}?\\textbf\{\$(\d+)\$ of \$(\d+)\$\}", 1),
    ("measure_e14.py", "S-versus-D relaxed count falsified",
     r"S scores \$(\d+)\$ of \$(\d+)\$", 1),
    ("measure_e14.py", "hard-network identity count falsified",
     r"bitwise identical\} on\s*\$(\d+)\$ of \$(\d+)\$ tasks", 1),
    ("measure_e13.py", "E13-2 caption hard rho falsified",
     r"the substance is unchanged \(diameter \$(-?[\d.]+)\$, placement", 1),
    ("measure_e13.py", "E13-5b informative-half rho falsified",
     r"informative half gives \$\\rho = (-?[\d.]+)\$", 1),
    ("measure_e13.py", "constant index rule's assignment rho falsified",
     r"constant index rule's \$\+?(-?[\d.]+)\$", 1),
    ("measure_e13.py", "constant rule's diameter rho falsified",
     r"constant rule above already reaches \$\+?(-?[\d.]+)\$", 1),
]


def raw_pattern(pattern: str) -> str:
    """The same anchor, tolerant of the source's hard wrapping.

    The gates match on whitespace-normalised text, so their anchors carry single spaces
    where the source may carry a newline: `constant index rule's $+0.431$` is written
    across two lines in main.tex.  Applying the poison to the RAW file (which is what the
    gates are handed) therefore needs every literal space widened to `\\s+`, or two of the
    nine poisons silently edit nothing and report themselves as failures.
    """
    return pattern.replace(" ", r"\s+")


def perturb(num: str) -> str:
    """A different number of the same shape, so the gate has to notice.

    Flips the last digit (0 -> 1, anything else -> 0) and keeps the sign, the decimal
    point and every other digit, so the result is still a well-formed value of the same
    magnitude: a poison that changed `-5.07` to `banana` would prove nothing about the
    numeric comparison.
    """
    idx = [i for i, ch in enumerate(num) if ch.isdigit()]
    if not idx:
        return num + "1"
    i = idx[-1]
    return num[:i] + ("1" if num[i] == "0" else "0") + num[i + 1:]


def run(gate, tex_path):
    cmd = [sys.executable, str(HERE / "scripts" / gate), "--tex", str(tex_path)]
    if gate == "measure_e13.py":
        cmd.append("--check")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    return r.returncode, (r.stdout + r.stderr)


def with_copy(src, mutate):
    """Run both gates against a temp copy of main.tex produced by `mutate`."""
    with tempfile.TemporaryDirectory() as d:
        poisoned = Path(d) / "main.tex"
        poisoned.write_text(mutate(src), encoding="utf-8")
        return {g: run(g, poisoned) for g in ("measure_e14.py", "measure_e13.py")}


def main() -> int:
    src = TEX.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", src)
    fails = 0

    # The baseline has to be green or a poison proves nothing.  This is also where the
    # clean-clone defect showed up first: measure_e13.py could only read a gitignored
    # record, so on a checkout it exited 2 here and every poison below went untested.
    base_rc, base_out = run("measure_e14.py", TEX)
    base13, base13_out = run("measure_e13.py", TEX)
    if base_rc or base13:
        print(f"BASELINE FAILS (e14 exit {base_rc}, e13 exit {base13}): the unpoisoned "
              "document must pass before a poison means anything")
        for line in (base_out + base13_out).splitlines():
            if line.strip().startswith("-"):
                print("       " + line.strip()[:150])
        return 1
    print("baseline: both gates pass on the unpoisoned document")

    # CONTROL: a change that alters no number must not make either gate fail.  Without
    # this, a poison suite whose temp-file machinery is broken reports every case as
    # "caught" and looks like a perfect gate.
    ctl = with_copy(src, lambda s: "% a comment line changes no number\n" + s)
    ctl_ok = all(rc == 0 for rc, _ in ctl.values())
    fails += not ctl_ok
    print(f"{'PASS' if ctl_ok else 'FAIL'} control: a no-op edit leaves both gates green "
          f"(e14 exit {ctl['measure_e14.py'][0]}, e13 exit {ctl['measure_e13.py'][0]})")

    for gate, name, pattern, group in POISONS:
        m = re.search(pattern, flat, re.S)
        n = len(re.findall(pattern, flat, re.S))
        if not m:
            print(f"FAIL {name}: anchor /{pattern}/ occurs 0 times, so this poison no "
                  "longer tests anything -- the prose moved or the anchor is wrong")
            fails += 1
            continue
        old = m.group(group)
        new = perturb(old)
        if new == old:
            print(f"FAIL {name}: perturbation of {old!r} produced itself")
            fails += 1
            continue

        def mutate(s, pattern=pattern, group=group, new=new):
            mm = re.search(raw_pattern(pattern), s, re.S)
            if mm is None:
                return s
            a, b = mm.span(group)
            return s[:a] + new + s[b:]

        poisoned = mutate(src)
        if poisoned == src:
            print(f"FAIL {name}: the poison did not change the document")
            fails += 1
            continue
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "main.tex"
            p.write_text(poisoned, encoding="utf-8")
            rc, out = run(gate, p)
        caught = rc != 0
        fails += not caught
        detail = ""
        if caught:
            for line in out.splitlines():
                if line.strip().startswith("-") and ("disagree" in line or old in line):
                    detail = line.strip()[:130]
                    break
        print(f"{'PASS' if caught else 'FAIL'} {name} ({n} anchor(s), {old} -> {new}): "
              f"{'CAUGHT' if caught else 'MISSED (exit 0)'}"
              + (f"\n       {detail}" if detail else ""))

    print(f"\n{'ALL CLAIM-SCOPED POISONS CAUGHT' if not fails else f'{fails} POISON(S) FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
