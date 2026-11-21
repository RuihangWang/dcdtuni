#!/usr/bin/env python3
"""Gate the measurement archive (Appendix `app:archive`) three ways at once.

The archive exists because compressing the body to the venue's page limit silently
deleted seventeen measurements, and the build stayed green: 0 errors, 0 overfull boxes,
0 undefined references, `check_manuscript.py` reporting CHECK OK.  Restoring them by hand
would repeat the campaign's own named failure mode -- "a number quoted from prose rather
than from a record was wrong every time it was checked" -- so an entry is admitted only
under three independent checks, each of which has been seen to fail:

  1. PROVENANCE.  Every token and every prose run the manifest names for an entry must be
     present in the pre-compression revision, read with `git show` at check time rather
     than copied into the manifest.  Editing the manifest cannot weaken this, and a
     paraphrase that reuses the right digits fails on its prose runs.
  2. PRESENCE.  Every token must be inside that entry's own `\\item` in main.tex, so a
     number cannot survive by appearing somewhere else in the document (the failure that
     let `2.95\\sigma` persist as an unexplained constant while its derivation was gone).
  3. RENDERING.  A distinctive prose probe from every item must be in the BUILT PDF, which
     is the check that catches LaTeX dropping content it cannot fit.

The id sets of the manifest and the section must agree in both directions: an entry with
no item is unrendered, and an item with no entry is ungated.  A manifest with no entries
fails rather than passing on an empty denominator.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE / "scripts"))
from pdf_content_gate import (PROBE_DIGIT_RUN_LIMIT, canon, probe,   # noqa: E402
                             strip_tex)

MIN_RESTORED = 16          # sixteen restored + one covered elsewhere = the seventeen found
SECTION_LABEL = "app:archive"
NUMERIC = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
# Derived from probe()'s bound rather than written beside it: this relaxation deletes
# exactly the digit runs a probe is forbidden to contain, and the two cannot drift.
LINE_NUMBERS = re.compile(r"\d{%d,}" % (PROBE_DIGIT_RUN_LIMIT + 1))

sys.path.insert(0, str(HERE / "scripts"))
from check_manuscript import RUNNING_HEAD   # noqa: E402


def pdf_canon_text(pdf: Path):
    """Canonical text of the built PDF, with the page furniture removed.

    This archive is flowing prose, so an item can straddle a page break, and `pdftotext
    -raw` emits the break's furniture in content-stream order -- the venue's line-number
    column as one concatenated digit run, then the running head -- straight into the
    middle of the paragraph.  Entry A08's item breaks between "projects on" and "disk:",
    so its probe is present in the PDF and absent from the naive extraction.  Removing the
    running head and digit runs of eight or more characters rejoins it.

    That relaxation is bounded, and the bound is checked rather than assumed: no probe may
    itself contain an eight-digit run, so the substitution cannot delete anything the gate
    is looking for.  `pdf_content_gate.py` does not need this because it probes captions
    and ledger rows, which live in floats that do not split mid-cell.
    """
    r = subprocess.run(["pdftotext", "-raw", str(pdf), "-"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"pdftotext failed: {r.stderr[:300]}")
    txt = canon(strip_tex(r.stdout))
    head = canon(strip_tex(RUNNING_HEAD))
    n_head, n_lnum = txt.count(head), len(LINE_NUMBERS.findall(txt))
    return txt.replace(head, "").replace("\x01", ""), LINE_NUMBERS.sub("", txt.replace(head, "")), n_head, n_lnum


def old_source(rev: str, path: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{rev}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            f"cannot read {rev}:{path} -- the archive's provenance check needs git "
            f"history and will not pass without it: {r.stderr.strip()[:200]}")
    return r.stdout


def archive_section(tex: str) -> str:
    """The archive section's source, from its label to the next \\section."""
    m = re.search(r"\\label\{" + SECTION_LABEL + r"\}", tex)
    if not m:
        raise SystemExit(f"main.tex has no \\label{{{SECTION_LABEL}}}: the archive "
                         f"section is gone, which is exactly what this gate exists to "
                         f"notice")
    rest = tex[m.end():]
    nxt = re.search(r"^\\section\{", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def section_span(tex: str) -> str:
    """The whole archive section, `\section{...}` up to but excluding the next one."""
    m = re.search(r"^\\section\{Measurement archive[^\n]*\n", tex, re.M)
    if not m:
        raise SystemExit("main.tex has no 'Measurement archive' section")
    rest = tex[m.start():]
    nxt = re.search(r"^\\section\{", rest[len(m.group(0)):], re.M)
    cut = len(m.group(0)) + (nxt.start() if nxt else len(rest))
    return rest[:cut].rstrip() + "\n"


def regenerate(builder: Path):
    """Run the builder into a scratch dir and return (tex, manifest, stderr)."""
    with tempfile.TemporaryDirectory(prefix="arch_fresh.") as d:
        r = subprocess.run([sys.executable, str(builder), "--out", d],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            return None, None, (r.stdout + r.stderr).strip()
        tex = (Path(d) / "archive_section.tex").read_text(encoding="utf-8")
        man = (Path(d) / "measurement_archive.json").read_text(encoding="utf-8")
    return tex.rstrip() + "\n", man, ""


def items(section: str) -> dict[str, str]:
    """{(Axx): source of that \\item, up to the next \\item or the list's end}."""
    out, marks = {}, [(m.group(1), m.start(), m.end())
                      for m in re.finditer(r"\\item\s*\\textbf\{\(([A-Z]\d\d)\)\}", section)]
    for k, (ident, s, e) in enumerate(marks):
        end = marks[k + 1][1] if k + 1 < len(marks) else len(section)
        tail = section[e:end]
        cut = tail.find("\\end{itemize}")
        out[ident] = tail[:cut] if cut >= 0 else tail
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(HERE / "build" / "main.pdf"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    ap.add_argument("--manifest", default=str(HERE / "records" / "measurement_archive.json"))
    ap.add_argument("--builder", default=str(HERE / "scripts" / "build_archive.py"))
    ap.add_argument("--no-fresh", action="store_true",
                    help="skip the generated-section freshness diff")
    a = ap.parse_args()

    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    tex = Path(a.tex).read_text(encoding="utf-8")
    section = archive_section(tex)
    got = items(section)
    old_canon = canon(strip_tex(old_source(man["old_revision"], man["old_path"])))

    pdf = Path(a.pdf)
    if not pdf.exists():
        print(f"MEASUREMENT ARCHIVE GATE: cannot run, {pdf} missing", file=sys.stderr)
        return 2
    _, pdf_canon, n_head, n_lnum = pdf_canon_text(pdf)

    fails, notes = [], []
    want = {e["id"]: e for e in man["restored"]}

    # Freshness, for the reason ledger row (27) gives: this section is a committed cached
    # copy of a generator's output, and a cached copy that nothing compares against its
    # generator goes stale silently -- twice, in the direction that hid a result.
    if not a.no_fresh:
        builder = Path(a.builder)
        if not builder.exists():
            fails.append(f"the archive's generator {builder.name} is missing, so the "
                         f"committed section is an unchecked cached copy")
        else:
            f_tex, f_man, err = regenerate(builder)
            if f_tex is None:
                fails.append(f"build_archive.py failed, so the archive cannot be shown "
                             f"current: {err.splitlines()[-1][:200] if err else 'no output'}")
            else:
                have = section_span(tex)
                if f_tex != have:
                    d = list(difflib.unified_diff(have.splitlines(), f_tex.splitlines(),
                                                  "committed section", "regenerated",
                                                  lineterm="", n=0))
                    fails.append(f"the archive section in main.tex differs from what "
                                 f"build_archive.py generates now, in "
                                 f"{len([x for x in d if x[:1] in '+-' and x[:3] not in ('---','+++')])} "
                                 f"lines: {d[2][:160] if len(d) > 2 else ''}")
                else:
                    notes.append("archive section is byte-identical to what "
                                 "build_archive.py generates now")
                if f_man != Path(a.manifest).read_text(encoding="utf-8"):
                    fails.append("records/measurement_archive.json differs from what "
                                 "build_archive.py generates now")
                else:
                    notes.append("manifest is byte-identical to what build_archive.py "
                                 "generates now")

    if len(want) < MIN_RESTORED:
        fails.append(f"the manifest names {len(want)} restored entries; the review found "
                     f"{MIN_RESTORED} lost measurements plus one restored by regeneration, "
                     f"so a smaller manifest means entries were dropped, not that fewer "
                     f"were lost")

    missing_items = sorted(set(want) - set(got))
    extra_items = sorted(set(got) - set(want))
    if missing_items:
        fails.append(f"manifest entries with no \\item in the archive section (an ungated "
                     f"number is a number that can vanish again): {missing_items}")
    if extra_items:
        fails.append(f"archive \\items the manifest does not name (hand-typed numbers with "
                     f"no provenance record): {extra_items}")

    for ident, e in sorted(want.items()):
        for p in (e.get("probe_tex", ""), e["probe_old"]) + tuple(e["prose_runs"]):
            if LINE_NUMBERS.search(p):
                fails.append(f"{ident}: probe {p[:32]!r} contains an eight-digit run, which "
                             f"the line-number relaxation would delete -- the gate would "
                             f"then be unable to see this entry either way")

    n_tok = n_runs = 0
    for ident, e in sorted(want.items()):
        src = got.get(ident)
        if src is None:
            continue
        # Tokens are checked against the RESTORED sentence, not the whole item.  The item
        # also carries a "why it matters" gloss written now, and a gloss that repeats a
        # number is not evidence the number survived: altering $2.95\sigma$ to $2.9\sigma$
        # inside A01's restored text passed a whole-item check, because A01's gloss quotes
        # $2.95\sigma$ twice.  That is precisely the failure this gate exists to catch --
        # a constant outliving its derivation -- arriving through the gloss.
        restored = src.split(r"\emph{Why it matters:}")[0]
        for t in e["old_lines"]:
            n_tok += 1
            if t not in restored:
                fails.append(f"{ident}: token {t!r} is in the manifest but not inside its "
                             f"own \\item -- it may survive elsewhere in the document, "
                             f"which is how 2.95 sigma outlived its derivation")
        for pr in e["prose_runs"]:
            n_runs += 1
            if pr not in old_canon:
                fails.append(f"{ident}: prose run {pr[:40]!r} is not in "
                             f"{man['old_revision']}, so this entry is not a restoration")
        p = e.get("probe_tex") or probe(restored,
                                        max_digit_run=PROBE_DIGIT_RUN_LIMIT)
        if not p:
            fails.append(f"{ident}: no prose run long enough to probe in the built PDF")
        elif p not in pdf_canon:
            fails.append(f"{ident}: not rendered -- probe {p!r} is absent from "
                         f"{pdf.name}, i.e. LaTeX dropped it or the section is stale")
        listed = set(e["old_lines"])
        unlisted = sorted({n for n in NUMERIC.findall(restored)
                           if not any(n in t for t in listed)})
        if unlisted:
            notes.append(f"{ident}: {len(unlisted)} numeric literals not named in the "
                         f"manifest ({', '.join(unlisted[:6])})")

    # the seventeenth: restored by regeneration, so it must actually be there
    for c in man.get("covered_elsewhere", []):
        f = HERE / c["where"]
        if not f.exists():
            fails.append(f"{c['id']}: {c['where']} does not exist, so the entry said to be "
                         f"covered by regeneration is covered by nothing")
        elif c["token"] not in f.read_text(encoding="utf-8"):
            fails.append(f"{c['id']}: {c['token']} is not in {c['where']}, so it was not "
                         f"restored by regeneration and is now lost a second time")
        else:
            notes.append(f"{c['id']}: {c['what']} confirmed present in {c['where']}")
        para = re.search(r"\\paragraph\{Restored by regeneration instead\.\}(.*?)(?=\n\n|\Z)",
                         section, re.S)
        if not para:
            fails.append("the archive section has no 'Restored by regeneration instead' "
                         "paragraph, so the seventeenth measurement is unaccounted for")
        else:
            p = probe(para.group(1), max_digit_run=PROBE_DIGIT_RUN_LIMIT)
            if p and p not in pdf_canon:
                fails.append(f"the regeneration paragraph is not rendered: {p!r}")

    if not got:
        fails.append("the archive section contains no \\item at all")

    for n in notes:
        print("note:", n)
    print(f"MEASUREMENT ARCHIVE GATE: {len(want)} manifest entries, {len(got)} items, "
          f"{n_tok} tokens pinned to their own item, {n_runs} prose runs pinned to "
          f"{man['old_revision']}, all probed against {pdf.name} "
          f"({n_head} running heads and {n_lnum} line-number runs removed as page furniture)")
    if fails:
        print("MEASUREMENT ARCHIVE GATE FAILED:", file=sys.stderr)
        for f in fails:
            print("  -", f, file=sys.stderr)
        return 1
    print("MEASUREMENT ARCHIVE GATE OK (provenance, presence and rendering all hold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
