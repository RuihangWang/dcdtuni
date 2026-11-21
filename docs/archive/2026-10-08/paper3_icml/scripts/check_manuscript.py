#!/usr/bin/env python3
"""Manuscript checks the Makefile's `check` target runs.

Exists because the Makefile pointed at this path for months while the file did not
exist (review, 2026-09-17).  It was then committed and its page gate was still
worthless: it read `/Count` out of the first 4 KB of the PDF, which this PDF does
not carry, printed `pages=None`, and PASSED a body that ran to nine pages.  A gate
that reports "I could not measure it" as success is worse than no gate, so every
check below either measures its quantity or fails.

Checks: no unfilled placeholders; no undefined references or citations; no
unreferenced float; every figure file present; the defect count in the section
title, the contributions list and the ledger table all agree; every bibliography
entry resolved against publisher metadata by `check_bibliography.py`; and
non-reference content inside the venue's page limit, with the page of the
References heading read out of the PDF's own text.

Every gate below is RUN here, and that is the point of this file rather than an
incidental property of it: ledger row (24) is about a correct check with no call
site, and the round that closed it left two more.  So the poison suites
(`test_canon.py`, `test_claim_scoped.py`, `poison_archive_gate.py`,
`poison_prose_counts.py`), the code-side scans (`check_ddl_withdrawn.py`), the
self-tests of the two benchC probes the ledger cites as evidence
(`sensitivity_task.py --self-test`, `probe_reproducibility.py --check`), the
derivation the appendix cites by name (`verify_eps_units.py --check`) and the
clean-clone regression (`check_clean_clone.py`) are all invoked from here.  A gate
whose file is MISSING fails loudly rather than being skipped, because "the file was
not there" and "the file was there and passed" must not print the same thing.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parents[2]
BODY_PAGE_LIMIT = 9            # Amendment 27: nine pages of technical content,
                               # references excluded.  Was ICML's eight; two
                               # independent round-4 reviews catalogued what the
                               # eighth page was costing (duplicated paragraphs with
                               # divergent wording, orphan \paragraph headings rendered
                               # into the shipped PDF, eleven ledger rows LaTeX dropped
                               # silently) and both put this artifact at NeurIPS
                               # Datasets & Benchmarks (nine pages plus unlimited
                               # references) or TMLR (none) rather than the ICML
                               # research track.  If the target moves back to ICML this
                               # constant is the only thing that has to change, and the
                               # body will have to lose 136 rendered lines to do it.
RUNNING_HEAD = "Data-Center Design Against Two Conflicting Objectives"


def run_gate(argv, cwd, notes, fails, label, finding, tail=6):
    """Run one gate script and fold its verdict into (notes, fails).

    The gate's own last lines are kept as notes and its `- `-prefixed findings are kept as
    the failure text, so a red suite says what is wrong without anyone re-running anything.
    """
    r = subprocess.run([sys.executable] + list(argv), capture_output=True, text=True,
                       cwd=str(cwd))
    out = (r.stdout or "") + (r.stderr or "")
    lines = [l for l in out.splitlines() if l.strip()]
    for l in lines[-tail:]:
        notes.append(f"{label}: {l.strip()}")
    if r.returncode != 0:
        detail = [l.strip()[2:] for l in lines if l.strip().startswith("- ")]
        fails.append(f"{finding}: " + ("; ".join(d[:200] for d in detail[:3]) if detail
                                      else (lines[-1][:250] if lines
                                            else f"exit {r.returncode}")))
    return r.returncode


def page_text(pdf: Path, page: int) -> str | None:
    """One page of extracted text, or None if it does not exist."""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page),
                            str(pdf), "-"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 and r.stdout.strip() else None
    try:
        from pypdf import PdfReader
    except ImportError:
        return False            # distinguishable from None: no extractor at all
    r = getattr(page_text, "_reader", None)
    if r is None or r._path is not pdf:
        r = PdfReader(str(pdf))
        r._path = pdf
        page_text._reader = r
    if page - 1 >= len(r.pages):
        return None
    return r.pages[page - 1].extract_text() or ""


def measure_body_pages(pdf: Path):
    """(body_pages, references_page).  body_pages counts a page as body content if
    any non-header text on it precedes the References heading."""
    page = 1
    while True:
        txt = page_text(pdf, page)
        if txt is False:
            return None, None, "no pdftotext and no pypdf: cannot measure the page count"
        if txt is None:
            return None, None, "reached the end of the PDF without finding 'References'"
        if "References" in txt:
            before = txt.split("References", 1)[0]
            spill = [ln.strip() for ln in before.splitlines()
                     if ln.strip() and RUNNING_HEAD not in ln and ln.strip() != str(page)]
            return (page if spill else page - 1), page, (
                f"{len(spill)} lines of body text precede the References heading on "
                f"page {page}: " + (spill[0][:80] if spill else ""))
        page += 1
        if page > 400:
            return None, None, "gave up after 400 pages"


def main() -> int:
    main_tex = (HERE / "main.tex").read_text(encoding="utf-8")
    gen = HERE / "gen_appendix.tex"
    gen_tex = gen.read_text(encoding="utf-8") if gen.exists() else ""
    body = main_tex + "\n" + gen_tex
    fails, notes = [], []

    # The deletion gate.  It had no call site for three rounds, which is row (24)'s
    # defect in the gate written to close row (24)'s class: a comparison of the numeric
    # tokens in main.tex and gen_appendix.tex against a tracked baseline revision, so a
    # compression that drops a measurement fails the build instead of being found by a
    # review round diffing two revisions by hand.
    # The gates' own poison suites.  A gate must be seen to fail before it is trusted, and
    # a poison test nobody runs is the dead-check defect in a new hat: each of these proves
    # a gate can fail, and each is the kind of file that survives a refactor and stops being
    # invoked.  An anchor that drifts makes a poison test nothing, which these report as a
    # failure rather than a skip.
    # poison_archive_gate.py and poison_prose_counts.py were dead too: both existed, both
    # passed when run by hand, and neither was invoked by anything.  They are wired here for
    # the reason the two below are.  poison_prose_counts.py had additionally rotted -- three
    # of its six anchors were literals transcribed from a ledger that has since grown from
    # 32 rows to 42, so three `.replace()` calls did nothing and the harness reported MISSED,
    # which reads as "the gate is weak" when the truth was "the poison was never applied".
    # Its anchors are derived from the document now.
    poison_archive = HERE / "scripts" / "poison_archive_gate.py"
    if not poison_archive.exists():
        fails.append("scripts/poison_archive_gate.py is missing: the measurement-archive "
                     "gate can no longer be shown to fail")
    else:
        run_gate([str(poison_archive), str(HERE)], HERE, notes, fails, "poison-archive",
                 "poison_archive_gate.py: the measurement-archive gate could not be shown "
                 "to fail, so it is unproven")

    poison_prose = HERE / "scripts" / "poison_prose_counts.py"
    if not poison_prose.exists():
        fails.append("scripts/poison_prose_counts.py is missing: the prose-count gate can "
                     "no longer be shown to fail")
    else:
        run_gate([str(poison_prose), str(HERE)], HERE, notes, fails, "poison-prose",
                 "poison_prose_counts.py: the prose-count gate could not be shown to fail, "
                 "so it is unproven")

    for t in ("test_canon.py", "test_claim_scoped.py"):
        tp = HERE / "scripts" / t
        if not tp.exists():
            fails.append(f"self-test {t} is missing; the gate it poisons is unproven")
            continue
        r = subprocess.run([sys.executable, str(tp)], capture_output=True, text=True,
                           cwd=str(REPO_ROOT))
        tail = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()][-3:]
        for line in tail:
            print("note: " + t + ": " + line)
        if r.returncode != 0:
            fails.append(f"self-test {t} failed (exit {r.returncode}): a gate can no longer "
                         "be shown to fail, or a poison's anchor drifted and now tests nothing")
        else:
            notes.append(f"{t}: every poison caught")

    # Every tracked record has a reader, or it is a mirror that can go stale silently.
    r = subprocess.run([sys.executable, str(HERE / "scripts" / "check_tracked_records.py")],
                       capture_output=True, text=True, cwd=str(HERE))
    for line in r.stdout.strip().splitlines():
        print("note: " + line)
    if r.returncode != 0:
        fails.append("tracked-record gate failed: a record committed as evidence disagrees "
                     "with the manuscript, is missing, or is quoted without its provenance")
    else:
        notes.append("tracked-record gate exit 0")

    baseline = HERE / "records" / "compression_baseline.json"
    if not baseline.exists():
        fails.append(f"no compression baseline at {baseline.relative_to(HERE)}: the "
                     "deletion gate has nothing to compare against")
    else:
        r = subprocess.run([sys.executable, str(HERE / "scripts" / "check_no_number_lost.py"),
                            "--config", str(baseline)],
                           capture_output=True, text=True, cwd=str(HERE))
        for line in (r.stdout + r.stderr).strip().splitlines():
            print("note: no-number-lost: " + line)
        if r.returncode != 0:
            fails.append(f"deletion gate failed (exit {r.returncode}): a number left the "
                         "document or thinned without a recorded reason")


    n_todo = len(re.findall(r"\\todo\{", body))
    if n_todo:
        fails.append(f"{n_todo} unfilled \\todo placeholders")

    log = HERE / "build" / "main.log"
    if log.exists():
        txt = log.read_text(encoding="utf-8", errors="replace")
        undef = len(re.findall(r"(Reference|Citation).*undefined", txt))
        if undef:
            fails.append(f"{undef} undefined references/citations in build/main.log")
        if re.search(r"^!", txt, re.M):
            fails.append("build/main.log contains LaTeX errors")
        over = len(re.findall(r"Overfull", txt))
        if over:
            notes.append(f"{over} overfull boxes")
    else:
        fails.append("build/main.log missing: run `make all` before `make check`")

    for m in re.findall(r"includegraphics[^{]*\{([^}]+)\}", body):
        if not (HERE / m).exists():
            fails.append(f"missing figure file {m}")

    # every label referenced, every float label referenced
    labels = set(re.findall(r"\\label\{([^}]+)\}", body))
    refs = set(re.findall(r"\\(?:ref|eqref|autoref)\{([^}]+)\}", body))
    dangling = sorted(refs - labels)
    if dangling:
        fails.append(f"\\ref to non-existent labels: {dangling}")
    # Only the hand-written body is held to "every float is \ref'd".  The appendix
    # is generated table-by-table from record JSONs by fill_results.py, which has no
    # prose to reference them from; requiring a \ref there would mean inventing one.
    main_labels = set(re.findall(r"\\label\{([^}]+)\}", main_tex))
    gen_labels = labels - main_labels
    for lab in sorted(main_labels):
        if lab.startswith(("fig:", "tab:")) and lab not in refs:
            fails.append(f"float label {lab} in main.tex is never \\ref'd (LaTeX does "
                         "not warn about this, so nothing else catches it)")
    unref_gen = sorted(l for l in gen_labels if l.startswith(("fig:", "tab:")) and l not in refs)
    if unref_gen:
        notes.append(f"{len(unref_gen)} generated appendix floats are not \\ref'd "
                     "(appendix tables are emitted from records and have no prose)")

    # Every count stated in prose, against the artifact it counts.
    #
    # The block this replaces was dead, and a review demonstrated it rather than argued it.
    # Its four patterns could not match this document -- `Lessons: (\w+) defects` fails on
    # the hyphen in "twenty-eight", and `(\w+) of the (\w+)---\(` fails on the same hyphen
    # -- so `counts` was empty, `nums` was empty, and neither assertion below could ever
    # fire, while the docstring at the top of this file said the three counts "all agree".
    # Its `ledger` variable parsed only the text before `\label{tab:defects}`, i.e. rows
    # 1-11 of a five-part table, so had `nums` been populated the check could only have
    # produced a false failure.  Its `rows` variable was computed and never used.
    #
    # This is the sixth instance of the campaign's own pattern -- a check whose denominator
    # can be zero passing on zero -- and it was inside the gate written to close that
    # pattern, so the replacement fails when it finds nothing to check.
    prose = HERE / "scripts" / "check_prose_counts.py"
    if prose.exists():
        r = subprocess.run([sys.executable, str(prose)], capture_output=True, text=True,
                           cwd=HERE)
        for line in r.stdout.strip().splitlines():
            notes.append(line[5:].strip() if line.startswith("note:") else line)
        if r.returncode != 0:
            err = [l for l in r.stderr.strip().splitlines() if l.strip().startswith("-")]
            fails.append("prose count gate: "
                         + (err[0][4:].strip() if err else r.stderr.strip()[:300] or "failed"))
            for l in err[1:4]:
                fails.append("prose count gate: " + l[4:].strip())
        else:
            notes.append("prose count gate exit 0")
    else:
        fails.append("scripts/check_prose_counts.py is missing, and nothing else compares a "
                     "count in prose with the thing it counts")

    # Defect (15) was a bibliography that was generated rather than looked up: an
    # arXiv id belonging to another paper, a survey that resolves to nothing, a
    # wrong first author and venue.  `and others` was the only symptom a regex
    # could see, so this now defers to check_bibliography.py, which resolves every
    # entry against Crossref/OpenAlex/arXiv and compares against a committed
    # record.  Offline: the record is the gate, so no build depends on network.
    bib = HERE / "references.bib"
    if bib.exists():
        r = subprocess.run([sys.executable, str(HERE / "scripts" / "check_bibliography.py")],
                           capture_output=True, text=True, cwd=HERE)
        # The gate's own verdict lines are not findings.  Treating every non-note line
        # as a failure made "BIBLIOGRAPHY CHECK OK (27 entries resolved...)" the reason
        # the manuscript check failed -- a gate that reports its own success as an error.
        for line in r.stdout.strip().splitlines():
            if line.startswith("note:"):
                notes.append(line[len("note:"):].strip())
            elif line.startswith(("BIBLIOGRAPHY CHECK OK", "BIBLIOGRAPHY CHECK FAILED")):
                notes.append(line)
            else:
                fails.append(line)
        if r.returncode != 0 and not r.stdout.strip():
            fails.append(f"check_bibliography.py crashed: {r.stderr.strip()[:300]}")
        elif r.returncode == 0:
            notes.append("bibliography gate exit 0")

    # Every other gate here checks a number against a record, and the records are
    # generated at build time, so the chain from disk to .tex is closed.  Nothing
    # closed the last step: a `table*` holding all twenty-seven ledger rows is
    # 1191.74 pt tall, LaTeX typesets what fits and silently drops the rest, and the
    # shipped PDF rendered sixteen rows with NO CAPTION while four places in the prose
    # said "all twenty-seven".  The build reported 0 errors, 0 overfull boxes and 0
    # undefined references.  Row (27) -- the defect about an appendix contradicting the
    # records on disk -- was itself invisible.  So the rendered artifact is now checked
    # too, and this is where it is wired in, because a gate nobody runs is the same
    # thing as a gate that does not exist (ledger row 24).
    pdfgate = HERE / "scripts" / "pdf_content_gate.py"
    if pdfgate.exists():
        r = subprocess.run([sys.executable, str(pdfgate)], capture_output=True, text=True,
                           cwd=HERE)
        for line in r.stdout.strip().splitlines():
            notes.append(line)
        if r.returncode != 0:
            fails.append(f"pdf content gate: {r.stderr.strip()[:600] or 'failed'}")
        else:
            notes.append("pdf content gate exit 0")

    # E13's table is the one place a number is typeset by hand rather than generated by
    # fill_results.py, so it gets its own comparison against its record.  Verified to
    # fail on a perturbed copy (rho_median 1.000 -> 0.900 and rho_min 0.999 -> 0.500 are
    # both reported, exit 1), because a gate that has not been seen to fail is not a gate.
    e13 = HERE / "scripts" / "measure_e13.py"
    if e13.exists():
        r = subprocess.run([sys.executable, str(e13), "--check", "--tex",
                            str(HERE / "main.tex")], capture_output=True, text=True,
                           cwd=HERE)
        for line in r.stdout.strip().splitlines():
            notes.append(line)
        if r.returncode != 0:
            fails.append(f"measure_e13.py --check: {r.stderr.strip()[:400] or 'failed'}")
        elif r.returncode == 0 and r.stdout.strip():
            notes.append("e13 numbers agree with results_e13.json")

    # The appendix is a COMMITTED CACHED COPY of what the records generate, and twice
    # now it has been shipped stale in the direction that hides a result: once printing
    # PENDING over a gate that had completed and failed, once printing "8 tasks, 8/8,
    # p = 0.0039" while ten records were on disk.  Ledger row (27) first claimed the
    # generator "has no cached copy", which was itself false, so the comparison is now a
    # gate rather than a discipline.  Non-destructive: it runs the generator against a
    # sandbox whose ddl is a symlink and whose docs is a real empty directory.
    fresh = HERE / "scripts" / "check_appendix_fresh.py"
    if fresh.exists():
        r = subprocess.run([sys.executable, str(fresh)], capture_output=True, text=True,
                           cwd=HERE)
        for line in (r.stdout.strip().splitlines() or []):
            notes.append(line)
        if r.returncode != 0:
            first = (r.stderr.strip().splitlines() or ["failed"])[0]
            fails.append(f"appendix freshness gate: {first}")
        else:
            notes.append("appendix freshness gate exit 0")

    # The pre-billing solid-identity gate, run over the archived E8 exports.  Wired in
    # because a correct function with no call site is exactly what ledger row (24) was
    # written about, and the row that closed it repeated the defect.
    solidab = REPO_ROOT / "ddl" / "case" / "benchB" / "E8_air_eval" / "check_solid_ab.py"
    if solidab.exists():
        r = subprocess.run([sys.executable, str(solidab), "--out",
                            str(HERE / "records" / "solid_ab_gate.json")],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        tail = [l for l in r.stdout.strip().splitlines() if l.startswith("  [")]
        for line in tail:
            notes.append("solid A/B gate:" + line)
        if r.returncode != 0:
            fails.append(f"solid A/B gate failed: {(tail or [r.stderr.strip()[:200]])[0]}")
        elif r.returncode == 2:
            notes.append("solid A/B gate skipped: archives not on this machine")
        else:
            notes.append("solid A/B gate exit 0")

    # E14's numbers are typeset in the body like E13's, so they get the same treatment:
    # a script that reads the record and compares, verified to fail on a copy with the
    # assignment win count changed from 1 of 8 to 3 of 8.
    e14 = HERE / "scripts" / "measure_e14.py"
    if e14.exists():
        r = subprocess.run([sys.executable, str(e14), "--tex", str(HERE / "main.tex")],
                           capture_output=True, text=True, cwd=HERE)
        for line in r.stdout.strip().splitlines():
            if line.startswith("  ") and "relaxed" in line:
                notes.append("e14:" + line.strip())
        if r.returncode != 0:
            fails.append(f"measure_e14.py --check: {r.stderr.strip()[:400] or 'failed'}")
        elif r.returncode == 0:
            notes.append("e14 numbers agree with results_e14.json (direction included)")

    # E15's numbers are typeset in the body like E13's and E14's, so they get the same
    # treatment.  A MISSING FILE IS A FAILURE, not a skip: `records/e15_place.json` is
    # committed as evidence and every number in the placement paragraph is prose-carried, so
    # if this script disappears nothing compares them and a silently-skipped gate is the
    # dead-check defect ledger row (24) is about.
    e15 = HERE / "scripts" / "measure_e15.py"
    if not e15.exists():
        fails.append("scripts/measure_e15.py is MISSING, so no gate reads "
                     "records/e15_place.json and every E15 number in the body is "
                     "unchecked.  Restore the script; this is a failure rather than a skip "
                     "because 'the gate was not there' and 'the gate passed' must not print "
                     "the same thing")
    else:
        r = subprocess.run([sys.executable, str(e15), "--check", "--tex",
                            str(HERE / "main.tex")], capture_output=True, text=True,
                           cwd=HERE)
        for line in ((r.stdout or "") + (r.stderr or "")).strip().splitlines():
            if line.strip().startswith(("e15:", "  ", "E15", "FAIL")):
                notes.append("e15: " + line.strip())
        if r.returncode != 0:
            err = [l.strip()[2:] for l in ((r.stdout or "") + (r.stderr or "")).splitlines()
                   if l.strip().startswith("- ")]
            fails.append("measure_e15.py --check: "
                         + ("; ".join(e[:200] for e in err[:3]) if err else
                            ((r.stderr or r.stdout).strip()[:400] or "failed")))
        else:
            notes.append("e15 numbers agree with records/e15_place.json")

    # The gates added for the agent result and for the completed E11e replication.  Both read
    # a tracked record and pin every number the body states against it, and neither had a call
    # site -- which is the dead-check defect this suite was rebuilt to remove, recurring in the
    # two newest gates.  A missing script is a failure, not a skip, so that "the gate was not
    # there" and "the gate passed" cannot print the same thing.
    for _name, _rec, _label in (("measure_e12.py", "e12_agent_result.json", "e12"),
                                ("measure_e11e.py", "e11e_24tasks.json", "e11e"),
                                ("measure_e10.py", "e10_adjoint.json", "e10"),
                                ("measure_costs.py", "oracle_costs.json", "costs"),
                                ("measure_v0.py", "e12_v0_audit.json", "v0"),
                                ("measure_e15_budget.py", "e15_place.json", "e15-budget")):
        _script = HERE / "scripts" / _name
        if not _script.exists():
            fails.append(f"scripts/{_name} is MISSING, so no gate reads records/{_rec} and every "
                         f"{_label} number in the body is unchecked.  Restore the script; this is "
                         "a failure rather than a skip")
        else:
            run_gate([str(_script), "--check", "--tex", str(HERE / "main.tex")], HERE,
                     notes, fails, _label, f"{_name} --check")

    # E17's record is not in records/: it lives beside the case that produced it, in a
    # tracked/ directory, because the archives it re-analyses are gitignored and a number
    # the manuscript quotes has to survive a clean clone.  Same rule as measure_e13.py.
    _e17 = HERE / "scripts" / "measure_e17.py"
    if not _e17.exists():
        fails.append("scripts/measure_e17.py is MISSING, so every E17 block-attribution number "
                     "in the manuscript is unchecked.  Restore the script; this is a failure "
                     "rather than a skip")
    else:
        run_gate([str(_e17), "--check", "--tex", str(HERE / "main.tex")], HERE, notes, fails,
                 "e17", "measure_e17.py --check")

    # A30.2c's three-arm table.  Wired now, while the third arm is still on the GPU,
    # in pending mode: with two arms in the record it states exactly what is owed and
    # passes, and becomes strict by itself when extract_agent_result.py is re-run with
    # --arms=ste_s0,grpo_s0,e12d_s0_r2.  A gate wired only after the run lands is a gate
    # that did not exist during the run, which is row (24).
    _e12t = HERE / "scripts" / "build_e12_table.py"
    if not _e12t.exists():
        fails.append("scripts/build_e12_table.py is MISSING, so nothing checks that the "
                     "three-arm table A30.2c requires is generated rather than typed")
    else:
        run_gate([str(_e12t), "--check", "--allow-pending", "--tex", str(HERE / "main.tex")],
                 HERE, notes, fails, "e12 three-arm table",
                 "e12 table gate: the three-arm table A30.2c commits to is either absent, "
                 "typed rather than generated, or built from a run that did not finish")

    # The two tests round 7 could not find.  Amendment 30.10 credits "a test of the
    # non-degenerate case" with catching a spearman fix that deleted `return num / den`;
    # that test was not in the repository, and re-deleting the line left the full suite
    # green.  test_v0_audit.py was called only from chain_eval.sh and exited 1 on a clean
    # clone because it read two gitignored archives, so it now has a hermetic mode.
    for _rel, _a, _lbl, _find in (
            ("ddl/case/benchB/E11f_adjoint_ablation/test_spearman.py", [],
             "spearman test", "E11f's spearman changed behaviour and its test noticed"),
            ("ddl/case/benchC/E12c_policy_eval/test_v0_audit.py", ["--self-test"],
             "v0 self-test", "the V0 audit's clauses changed and its hermetic self-test noticed")):
        _f = REPO_ROOT / _rel
        if not _f.exists():
            fails.append(f"{_rel} is MISSING, so the {_lbl} does not run and its absence "
                         "prints as a pass")
        else:
            run_gate([str(_f)] + _a, REPO_ROOT, notes, fails, _lbl, _find)

    # E17's own self-test and poison harness.  Both are wired rather than left beside the
    # experiment, because the two defects that building them found -- a decisive statistic
    # with no test, and an unreachable support clamp -- were invisible to every other check.
    _e17dir = REPO_ROOT / "ddl" / "case" / "benchB" / "E17_block_attribution"
    for _n, _a, _lbl, _find in (
            ("run.py", ["--self-test"], "e17 self-test",
             "E17's statistics changed behaviour and its 55 self-tests noticed"),
            ("poison_e17.py", [], "e17 poison",
             "E17's poison harness reports a break it cannot catch, so at least one statistic "
             "is unprotected")):
        _f = _e17dir / _n
        if not _f.exists():
            fails.append(f"{_f.relative_to(REPO_ROOT)} is MISSING, so the E17 "
                         f"{_lbl} does not run and its absence prints as a pass")
        else:
            run_gate([str(_f)] + _a, REPO_ROOT, notes, fails, _lbl, _find)

    # Sentence-anchored gates cannot see a SECOND statement of the same number: a round-6 review
    # shifted nine numbers by a factor of ten in sites no anchor covered and every gate stayed
    # green.  This one is value-anchored instead -- it takes every M x 10^E in the body and
    # requires that value to exist in a tracked record, wherever in the document it appears.
    run_gate([str(HERE / "scripts" / "check_sci_values.py"), "--check", "--tex",
              str(HERE / "main.tex")], HERE, notes, fails, "sci values",
             "scientific-notation gate: a value in the document has no tracked record behind it, "
             "so it is either prose-carried or has been shifted")

    # The withdrawn-phrase scan over the CASE CODE.  measure_e14.py has carried the phrase
    # list since row (35) and scanned main.tex only, while the rows that withdraw each
    # phrase cite scripts under ddl/case/ as the evidence -- so all of them survived there,
    # asserted as fact, in the files a reader is pointed at.
    run_gate([str(HERE / "scripts" / "check_ddl_withdrawn.py")], REPO_ROOT, notes, fails,
             "ddl withdrawn phrases",
             "withdrawn-phrase gate: a phrase the manuscript withdrew is still asserted by "
             "the code the ledger cites as the correction")

    # The two benchC probes the ledger cites as evidence.  Both had no call site, and one of
    # them (probe_reproducibility.py) exited 1 printing "0 of 8 base objectives reproduce
    # bitwise" while never printing a deviation -- so the number row (30) quotes from it had
    # no source.  Only their hermetic modes are wired: the measuring modes need gitignored
    # archives and would make the suite red on a clean clone, which is what
    # check_clean_clone.py exists to catch.
    sens = REPO_ROOT / "ddl" / "case" / "benchC" / "sensitivity_task.py"
    if not sens.exists():
        fails.append("ddl/case/benchC/sensitivity_task.py is missing, but ledger row (30) "
                     "cites it as the evidence that no verdict depends on the task that no "
                     "longer solves")
    else:
        run_gate([str(sens), "--self-test"], REPO_ROOT, notes, fails, "sensitivity",
                 "sensitivity_task.py --self-test: the scope this script discloses and the "
                 "contrast it actually computes have drifted apart, so what row (30) says "
                 "it covers is no longer what it covers")

    probe = REPO_ROOT / "ddl" / "case" / "benchC" / "probe_reproducibility.py"
    if not probe.exists():
        fails.append("ddl/case/benchC/probe_reproducibility.py is missing, but ledger row "
                     "(30) cites it for the per-task reproduction deviations")
    else:
        rc = run_gate([str(probe), "--check", "--tex", str(HERE / "main.tex")], REPO_ROOT,
                      notes, fails, "probe reproducibility",
                      "probe_reproducibility.py --check: the deviations row (30) quotes do "
                      "not match its tracked record, or the manuscript misquotes it")
        run_gate([str(probe), "--self-test"], REPO_ROOT, notes, fails, "probe self-test",
                 "probe_reproducibility.py --self-test: its stated tolerance or its "
                 "withdrawn claims no longer match what row (30) records")

    # The derivation Appendix C names: "the committed scripts/verify_eps_units.py derives
    # every count and p at both units and both readings of an undefined tau".  It had no
    # call site, so a paper cited a script as the source of its numbers that nothing
    # compared with them.  --check reads the COMMITTED snapshot of E11b's archive, so it is
    # hermetic.
    eps = HERE / "scripts" / "verify_eps_units.py"
    if not eps.exists():
        fails.append("scripts/verify_eps_units.py is missing, but Appendix C names it as "
                     "the derivation of both withdrawn cell-level counts and of the "
                     "task-level statistic")
    else:
        run_gate([str(eps), "--check", "--tex", str(HERE / "main.tex")], HERE, notes,
                 fails, "eps units",
                 "verify_eps_units.py --check: a count or p the manuscript states is not "
                 "what the cited derivation gives")

    # The floor-ratio producer.  Dead until now: the manuscript quotes six ratios from it
    # (66.8x, 26.6x, 8.9x, 90x, 13x and the 0.043 K two-sample range they all divide by),
    # its own docstring says one of them was hand-typed wrong once already, and nothing ran
    # it.  --check is the hermetic half and reads its TRACKED output; the measuring half
    # needs two gitignored archives and stays a manual step, which its docstring now says.
    ratios = REPO_ROOT / "ddl" / "case" / "benchB" / "E11c_reeval" / "floor_ratios.py"
    if not ratios.exists():
        fails.append("ddl/case/benchB/E11c_reeval/floor_ratios.py is missing, so the six "
                     "noise-floor ratios the manuscript quotes have no producer and no gate")
    else:
        run_gate([str(ratios), "--check", "--tex", str(HERE / "main.tex")], REPO_ROOT,
                 notes, fails, "floor ratios",
                 "floor_ratios.py --check: a noise-floor ratio the manuscript quotes is "
                 "not what its tracked record gives")

    # The clean-clone regression.  LAST, because it re-runs everything above inside a fresh
    # checkout and is the only check that can see a gate passing on untracked data.  It sets
    # DCD_CLEAN_CLONE_DEPTH so the nested run skips THIS gate and says so, rather than
    # recursing.
    if int(os.environ.get("DCD_CLEAN_CLONE_DEPTH", "0")) > 0:
        notes.append("clean-clone gate SKIPPED: this run is already inside a clean clone "
                     "(DCD_CLEAN_CLONE_DEPTH="
                     + os.environ["DCD_CLEAN_CLONE_DEPTH"] + "), so cloning again would "
                     "recurse; the outer run is the one that checks it")
    else:
        clean = HERE / "scripts" / "check_clean_clone.py"
        if not clean.exists():
            fails.append("scripts/check_clean_clone.py is missing, so nothing tests that "
                         "this suite is green on a clean checkout -- the failure mode that "
                         "let three findings ship behind a green local run")
        else:
            run_gate([str(clean)], REPO_ROOT, notes, fails, "clean-clone",
                     "clean-clone gate: the suite is RED on a clean clone of HEAD, i.e. it "
                     "passes here only because of files that are not committed", tail=10)

    # The measurement archive.  Compressing the body to the page limit silently deleted
    # seventeen measurements and the build stayed green, so restoring them by hand would
    # repeat the campaign's own named failure mode -- a number carried across by prose
    # rather than read out of a record.  The gate therefore checks three things at once:
    # that every token and prose run is in the pre-compression revision (read with
    # `git show` at check time, so editing the manifest cannot weaken it), that every token
    # is inside its OWN \item (a number surviving elsewhere in the document is how
    # 2.95 sigma outlived its derivation), and that a probe from every item is in the BUILT
    # PDF.  Wired in here for the reason ledger row (24) states: a gate nobody runs is the
    # same thing as a gate that does not exist.
    arch = HERE / "scripts" / "check_measurement_archive.py"
    if arch.exists():
        r = subprocess.run([sys.executable, str(arch)], capture_output=True, text=True,
                           cwd=HERE)
        for line in r.stdout.strip().splitlines():
            notes.append(line[5:].strip() if line.startswith("note:") else line)
        if r.returncode == 2:
            notes.append("measurement archive gate skipped: no built PDF yet")
        elif r.returncode != 0:
            err = [l for l in r.stderr.strip().splitlines() if l.strip().startswith("-")]
            fails.append("measurement archive gate: "
                         + (err[0][4:].strip() if err else r.stderr.strip()[:300] or "failed"))
            for l in err[1:4]:
                fails.append("measurement archive gate: " + l[4:].strip())
        else:
            notes.append("measurement archive gate exit 0")

    pdf = HERE / "build" / "main.pdf"
    pages = refpage = None
    if pdf.exists():
        pages, refpage, why = measure_body_pages(pdf)
        if pages is None:
            fails.append(f"page gate could not measure the body length: {why}")
        elif pages > BODY_PAGE_LIMIT:
            fails.append(f"{pages} pages of non-reference content exceeds the "
                         f"{BODY_PAGE_LIMIT}-page limit ({why})")
    else:
        fails.append("build/main.pdf missing: run `make all` before `make check`")

    # The tracked main.pdf must be the PDF these gates just ran on.  A review found the two
    # three pages apart, with the tracked copy carrying a different E11e task count, and
    # nothing compared them: `make` had no target that produced the tracked file.  Byte
    # identity is not the test and cannot be -- pdfTeX writes a random /ID and a
    # /CreationDate and nothing here sets SOURCE_DATE_EPOCH -- so the comparison is on page
    # count and canonicalised extracted text, which is the test that detects staleness and
    # is satisfiable.  Run `make ship` to satisfy it.
    shipped = HERE / "scripts" / "check_shipped_pdf.py"
    if shipped.exists():
        r = subprocess.run([sys.executable, str(shipped)], capture_output=True, text=True,
                           cwd=HERE)
        for line in r.stdout.strip().splitlines():
            notes.append(line)
        if r.returncode == 2:
            notes.append("shipped-PDF gate skipped: build/main.pdf missing")
        elif r.returncode != 0:
            err = [l.strip()[4:] for l in r.stderr.strip().splitlines()
                   if l.strip().startswith("- ")]
            fails.append("shipped-PDF gate: " + (err[0][:200] if err else "failed"))
        else:
            notes.append("shipped-PDF gate exit 0")

    for n in notes:
        print("note:", n)
    if fails:
        print("CHECK FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print(f"CHECK OK (todos=0, figures present, all floats \\ref'd, bibliography "
          f"resolved, body={pages} pages <= {BODY_PAGE_LIMIT}, References start on p.{refpage})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
