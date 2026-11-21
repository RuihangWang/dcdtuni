#!/usr/bin/env python3
"""Poison the measurement-archive gate eight ways; every one must exit non-zero.

A gate that has not been seen to fail is not a gate.  Each poison is one way the archive
could rot: a token lifted out of its own item, a digit altered in place, an item deleted
outright, the manifest's provenance weakened, the manifest emptied, a hand-typed item
added with no manifest entry behind it, and a `covered_elsewhere` entry pointing at a token
that is not there.

THIS FILE HAD NO CALL SITE, which is the defect `check_manuscript.py` wires two OTHER
poison suites in to avoid -- "a poison test nobody runs is the dead-check defect in a new
hat" -- while this one sat beside them unread.  It is wired in now.

The poisoned copies are written to a temporary directory.  They used to be written into the
manuscript directory itself as `p_<tag>.tex` / `p_<tag>.json` and unlinked afterwards, so a
harness run interrupted between the write and the unlink left junk beside the tracked
sources -- and, since it writes into a directory other people are editing, it could collide
with a concurrent build.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

R = Path(sys.argv[1])
T, M = R / "main.tex", R / "records" / "measurement_archive.json"
base_t, base_m = T.read_text(), M.read_text()
GATE = R / "scripts" / "check_measurement_archive.py"
SCRATCH = Path(tempfile.mkdtemp(prefix="poison_archive."))


def run(tag, tex=None, man=None, expect_fail=True):
    t = SCRATCH / f"p_{tag}.tex"; t.write_text(tex if tex is not None else base_t)
    m = SCRATCH / f"p_{tag}.json"; m.write_text(man if man is not None else base_m)
    r = subprocess.run([sys.executable, str(GATE), "--tex", str(t), "--manifest", str(m),
                        "--pdf", str(R / "build" / "main.pdf")],
                       capture_output=True, text=True, cwd=str(R))
    err = [l.strip()[2:] for l in r.stderr.splitlines() if l.strip().startswith("-")]
    ok = (r.returncode != 0) == expect_fail
    print(f"{'CAUGHT ' if ok else 'MISSED '}{tag:32s} exit={r.returncode}  "
          f"{(err[0][:118] if err else (r.stdout.splitlines() or [''])[-1][:118])}")
    t.unlink(); m.unlink()
    return ok


res = []
res.append(run("baseline_unpoisoned", expect_fail=False))


def must_have(needle, tag):
    """A poison whose anchor is gone tests nothing and must not report CAUGHT."""
    if base_t.count(needle) < 1:
        raise SystemExit(f"HARNESS FAILURE: {tag!r} needs {needle!r} in main.tex and it is "
                         f"not there; the prose moved and this poison has to be re-derived")


must_have("into $2.95\\sigma$", "digit_altered_in_item")
must_have("238 damped-Newton rescues", "token_lifted_out_of_item")
res.append(run("digit_altered_in_item",
               tex=base_t.replace("into $2.95\\sigma$", "into $2.9\\sigma$", 1)))
res.append(run("token_lifted_out_of_item",
               tex=base_t.replace("238 damped-Newton rescues", "damped-Newton rescues", 1)
                   + "\n% 238 damped-Newton rescues, moved out of its item\n"))
i = base_t.find("\\item \\textbf{(A11)}"); j = base_t.find("\\item \\textbf{(A12)}")
assert 0 < i < j, (i, j)
res.append(run("item_deleted", tex=base_t[:i] + base_t[j:]))
mn = json.loads(base_m)
assert mn.get("restored"), "the manifest has no `restored` entries to weaken"
mn["restored"][0]["prose_runs"] = ["thisprosewasneverintheoldrevision"]
res.append(run("manifest_provenance_weakened", man=json.dumps(mn)))
mn2 = json.loads(base_m); mn2["restored"] = []
res.append(run("manifest_emptied", man=json.dumps(mn2)))
# Anchor inside the ARCHIVE section, not on the first \end{itemize} in the file.
# It used to be the first, and the moment another section grew an itemize above it
# the poison started injecting into that one instead -- it reported MISSED and the
# harness dropped to 7/8 for a reason that had nothing to do with the archive gate.
_arch = base_t.find("\\section{Measurement archive")
assert _arch > 0, "the archive section moved or was renamed; this poison has no target"
_end = base_t.find("\\end{itemize}", _arch)
assert _end > _arch, "the archive section has no itemize to inject into"
res.append(run("hand_typed_item_no_manifest",
               tex=base_t[:_end] + "\\item \\textbf{(A99)} a hand-typed $3.14$ with no "
               "provenance behind it.\n" + base_t[_end:]))
mn3 = json.loads(base_m)
assert mn3.get("covered_elsewhere"), "the manifest has no `covered_elsewhere` entries"
mn3["covered_elsewhere"][0]["token"] = "999.9"
res.append(run("covered_entry_not_actually_there", man=json.dumps(mn3)))

print(f"\n{sum(res)}/{len(res)} poison tests behaved as designed")
SCRATCH.rmdir()
raise SystemExit(0 if all(res) else 1)
