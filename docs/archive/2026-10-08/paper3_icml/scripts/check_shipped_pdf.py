#!/usr/bin/env python3
"""The PDF a reader downloads must be the PDF the gates ran on, and both must come from
the source that is committed.

A review found the two disagreeing: `docs/manuscripts/paper3_icml/main.pdf` was a 23-page
build from 05:56 while `build/main.pdf`, the one every gate reads, was 25 pages from 06:24
and carried a different E11e task count.  Nothing compared them, `Makefile` had no target
that produced the tracked copy, and so the artifact a reader downloads was not the artifact
the gates certified -- which is the tracked/committed form of the defect the rendered-PDF
gate was written for.

Comparing `main.pdf` with `build/main.pdf` closes that, and then closes nothing else, because
`make ship` is `cp build/main.pdf main.pdf`: the two sides of the comparison are the same
file by construction and the gate is a tautology on any machine where ship was run.  What it
never asked is where `build/main.pdf` CAME FROM.  Editing main.tex and not rebuilding left
a PDF certifying a document that no longer existed, and the gate reported OK.  So this now
checks three separate things:

  1. PROVENANCE IN THE WORKING TREE -- `build/main.pdf` must be newer than every source
     that goes into it (`main.tex` and the generated `gen_appendix.tex`), otherwise it was
     not built from them;
  2. THE SHIPPED COPY -- the working tree's `main.pdf` must be the built one;
  3. THE COMMITTED COPY -- `git show HEAD:docs/manuscripts/paper3_icml/main.pdf` must be
     the built one, so a PDF that has been shipped but not committed cannot be certified.
     This gate reads files on disk and its verdict used to be reported in the language of
     what is committed ("the tracked main.pdf is the built one") while checking nothing of
     the kind; `git ls-files` and `git show` are what make the word "tracked" mean
     something, and both are read at check time so no local state can weaken them.

Byte identity is not the test and cannot be: two consecutive builds of unchanged source
differ, because pdfTeX writes a random `/ID` and a `/CreationDate` and nothing here sets
`SOURCE_DATE_EPOCH`.  What must be identical is the CONTENT, so the comparison is on page
count and on canonicalised extracted text -- the same comparison that detects staleness,
and the one that is satisfiable.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
REL = "docs/manuscripts/paper3_icml"
sys.path.insert(0, str(HERE / "scripts"))
from pdf_content_gate import canon, strip_tex   # noqa: E402


def read(pdf: Path):
    r = subprocess.run(["pdftotext", "-raw", str(pdf), "-"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"pdftotext failed on {pdf}: {r.stderr[:200]}")
    n = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
    pages = next((l.split(":")[1].strip() for l in n.stdout.splitlines()
                  if l.startswith("Pages:")), "?")
    return pages, canon(strip_tex(r.stdout))


LFS_POINTER = b"version https://git-lfs"


def committed_pdf(name: str):
    """(path, revision) for `git show HEAD:<name>`, materialised in a temp file.

    Returns (None, why) when the committed copy cannot be read, which is a FAILURE and not
    a skip: a gate that cannot see the committed artifact cannot certify it.

    `*.pdf` is a git-LFS path in this repository (.gitattributes), so `git show` returns
    the 131-byte POINTER rather than the document.  Reading that as a PDF gave
    "Couldn't find trailer dictionary" and a gate that failed for a reason nobody could
    act on, so the pointer is smudged through `git lfs smudge` -- which reads the object
    out of the local LFS store and needs no network.
    """
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, f"not inside a git working tree ({r.stderr.strip()[:120]})"
    rev = r.stdout.strip()
    spec = f"HEAD:{REL}/{name}"
    blob = subprocess.run(["git", "-C", str(REPO), "show", spec], capture_output=True)
    if blob.returncode != 0:
        return None, (f"`git show {spec}` failed "
                      f"({blob.stderr.decode('utf-8', 'replace').strip()[:160]}), so there "
                      f"is no committed copy to certify against")
    data = blob.stdout
    if data.startswith(LFS_POINTER):
        sm = subprocess.run(["git", "-C", str(REPO), "lfs", "smudge"], input=data,
                            capture_output=True)
        if sm.returncode != 0 or not sm.stdout.startswith(b"%PDF"):
            return None, (f"{spec} is a git-LFS pointer and `git lfs smudge` could not "
                          f"materialise it "
                          f"({sm.stderr.decode('utf-8', 'replace').strip()[:160]}), so the "
                          f"committed PDF cannot be read; run `git lfs pull`")
        data = sm.stdout
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    f.write(data)
    f.close()
    return Path(f.name), rev


def main() -> int:
    shipped, built = HERE / "main.pdf", HERE / "build" / "main.pdf"
    if not built.exists():
        print(f"SHIPPED PDF GATE: cannot run, {built} missing (run `make all`)",
              file=sys.stderr)
        return 2
    problems = []
    tmp = []

    # 1. provenance in the working tree: the built PDF must postdate its sources.
    built_mtime = built.stat().st_mtime
    for name in ("main.tex", "gen_appendix.tex"):
        src = HERE / name
        if not src.exists():
            continue
        if src.stat().st_mtime > built_mtime:
            problems.append(
                f"{name} is NEWER than build/{built.name} ({src.stat().st_mtime - built_mtime:.0f}s), "
                f"so the PDF every other gate reads was not built from the source on disk; "
                f"run `make all`")

    # 2. the shipped working-tree copy.
    if not shipped.exists():
        problems.append(f"{shipped} is not there at all, so what a reader downloads is "
                        f"nothing; run `make ship`")
        ps = ts = None
    else:
        ps, ts = read(shipped)
    pb, tb = read(built)
    if ts is not None and not (ps == pb and ts == tb):
        problems.append(f"the working tree's main.pdf is not the PDF the gates ran on: "
                        f"tracked {ps} pages vs built {pb} pages"
                        + ("" if ts == tb else ", and the extracted text differs"))

    # 3. the committed copy.
    cp, why = committed_pdf("main.pdf")
    if cp is None:
        problems.append(f"the committed main.pdf could not be read, so an uncommitted PDF "
                        f"cannot be ruled out: {why}")
        rev = "?"
    else:
        tmp.append(cp)
        rev = why
        pc, tc = read(cp)
        if not (pc == pb and tc == tb):
            problems.append(f"the COMMITTED main.pdf at HEAD {rev} is not the PDF the gates "
                            f"ran on: committed {pc} pages vs built {pb} pages"
                            + ("" if tc == tb else ", and the extracted text differs")
                            + "; run `make ship` and commit")

    if ts is not None and tb != ts:
        # In CHARACTERS, not words: both sides are canonicalised and canon() removes every
        # space, so a whole 32-page paper is one "word" and a bound of eight words is a
        # bound of nothing.  This printed 55,000 tokens of gate output over a three-page
        # difference before it was fixed.
        w = 160
        k = next((i for i in range(min(len(ts), len(tb))) if ts[i] != tb[i]),
                 min(len(ts), len(tb)))
        lo = max(0, k - w)
        print(f"  - extracted text differs at character {k} "
              f"(tracked {len(ts)} chars, built {len(tb)}):\n"
              f"      tracked ...{ts[lo:k + w]}...\n"
              f"      built   ...{tb[lo:k + w]}...", file=sys.stderr)

    for p in tmp:
        try:
            p.unlink()
        except OSError:
            pass

    if problems:
        print("SHIPPED PDF GATE FAILED:", file=sys.stderr)
        for x in problems:
            print("  -", x, file=sys.stderr)
        print("  - run `make all && make ship` (ship copies build/main.pdf to main.pdf) "
              "and commit", file=sys.stderr)
        return 1
    print(f"SHIPPED PDF GATE OK (build/{built.name} postdates its sources, the working "
          f"tree's main.pdf is that PDF, and so is the one committed at HEAD {rev}: "
          f"{pb} pages, identical extracted text)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
