#!/usr/bin/env python3
"""Build the measurement archive: one source of truth -> .tex section + manifest.

Every restored number must be found, with its line number, in the pre-compression
revision of main.tex (9135117d).  A number that is not in that file cannot be written
into the manifest, so the archive is provably a restoration and not new typing -- which
matters because "a number quoted from prose rather than from a record was wrong every
time it was checked" is this campaign's own named failure mode (Appendix D).
"""
from __future__ import annotations
import argparse
import json, re, subprocess, sys
from pathlib import Path

OLD_REV = "9135117d"
# Derived from this file, NOT from `Path.home() / "mycode" / "dcdtuni"`.  The hardcoded home
# meant this builder always imported `pdf_content_gate` from, and always ran `git show`
# against, the AUTHOR'S checkout no matter which tree it was invoked from -- so in a clean
# clone it regenerated the manifest using a different probe() than the one the clone's own
# gate was checking against, and failed with an ImportError naming a path outside the clone.
# A gate that only works on one machine is the defect this repository keeps finding.
SCRIPTS = Path(__file__).resolve().parent
PAPER = SCRIPTS.parent
REPO = PAPER.parents[2]
sys.path.insert(0, str(SCRIPTS))
from pdf_content_gate import (MARK, PROBE_DIGIT_RUN_LIMIT, canon,   # noqa: E402
                              probe, strip_tex)


def prose_runs(tex: str, want: int = 20) -> list[str]:
    """Every run of literal prose in `tex`, canonicalised, longest-first.

    `probe()` returns only the longest run, which pins one sentence and leaves the rest of
    the entry free to be reworded: a poison that changed "the one failing design" to "the
    single failing design" sailed through, because the longest run was elsewhere in the
    item.  Requiring every run closes that, and it is cheap -- an entry has three or four.
    """
    t = re.sub(r"\$[^$]*\$", MARK, tex)
    t = re.sub(r"\\(?:ref|eqref|autoref|Cref|cref)\{[^}]*\}", MARK, t)
    t = re.sub(r"\\S(?![a-zA-Z])", MARK, t)
    t = re.sub(r"\\[a-zA-Z]+\*?", " ", t)
    t = re.sub(r"[{}~_^&%]", " ", t)
    runs = [canon(strip_tex(r)) for r in t.split(MARK)]
    # Deterministic order, and not by accident: sorting a SET of equal-length strings by
    # length alone leaves their relative order to the hash seed, so two runs of the same
    # length swapped places between processes and the freshness gate -- correctly --
    # reported the committed manifest as differing from a regeneration of itself.  Tie on
    # the string, not on insertion order.
    runs = sorted({r for r in runs if len(r) >= want}, key=lambda r: (-len(r), r))
    return runs

# ---------------------------------------------------------------- the entries.
# `tex`   : the restored measurement, verbatim from the pre-compression revision
#           where quotation marks are used, with the sentence it belonged to.
# `tokens`: substrings that must be found in OLD.tex at the recorded lines, and in
#           the built archive item.  These are what the gate pins.
# `why`   : what the number is evidence for, and where the body still depends on it.
E = []
def add(id, title, tex, tokens, why):
    E.append(dict(id=id, title=title, tex=tex, tokens=tokens, why=why))

add("A01", "Defect (8): the corrected denominator, in full",
    r"The re-measurement gate compared a mean of $R=3$ replicates against a single "
    r"stored value at $3\sigma$, omitting that value's own variance; the correct "
    r"$\sigma\sqrt{1 + 1/R} = 1.155\sigma$ turns the one failing design's $3.41\sigma$ "
    r"into $2.95\sigma$, family-wise $7.3\%$ (normal) or $11.1\%$ ($t(48)$), both above "
    r"$\alpha$.  Both verdicts are archived, the pre-registered one as \emph{failed}.",
    ["1 + 1/R", "1.155", "3.41", "2.95", "7.3", "11.1", "t(48)"],
    r"This is the derivation of the $2.95\sigma$ that "
    r"Appendix~\ref{app:derivations} quotes as a bare constant when it argues that a "
    r"mis-pairing control reaches a minimum of $0.3\sigma$.  Without it the reader has "
    r"no way to know that $2.95\sigma$ is a \emph{pass} under the corrected gate and a "
    r"fail under the pre-registered one, which is the whole content of ledger row (8).")

add("A02", "The two margins closest to the propagated noise floor",
    r"De-biasing moves the median S$-$R margin from $-19.93$ to $-19.98$, a change of "
    r"$-0.053 \pm 0.108$ (95\% CI $[-1.33\%, +0.80\%]$, i.e.\ $<1.4\%$); the two margins "
    r"closest to the propagated floor move $3.03 \to 3.00$ and $3.53 \to 3.68$.",
    ["-19.93", "-19.98", "-0.053", "0.108", "-1.33", "+0.80", "<1.4",
     r"3.03 \to 3.00", r"3.53 \to 3.68"],
    r"Table~\ref{tab:e11b}'s caption states the conclusion---that the replicate-averaged "
    r"re-measurement changes the median margin ``by less than $1.4\%$''---but the "
    r"compression deleted the two margins the conclusion is about.  They are the smallest "
    r"in the table ($-3.03$ on task 900049 and $-3.53$ on 900053), i.e.\ the two the "
    r"$5\times$ pre-registered bar could plausibly have been breached on, so they are the "
    r"only two a reader would want to see move.")

add("A03", "Solver quality behind the assignment-block ranking",
    r"(5 edits skipped, 238 damped-Newton rescues)",
    ["5 edits skipped", "238 damped-Newton"],
    r"These qualify the $\rho = 0.518$ / sign $0.756$ over 12 designs that "
    r"\S\ref{sec:relax} quotes from the superseded E0 record for the \textbf{assignment} "
    r"block---the block E13 later measured at $\rho = 0.166$ and E14 at $1/8$.  A "
    r"rank correlation computed after 238 line-search rescues and 5 skipped edits is not "
    r"the same measurement as one from a clean solve, and the body quotes the correlation "
    r"without them.")

add("A04", "The counter-measurement that withdrew the pixel-separability claim",
    r"The largest real design difference on record---the two arms' selected designs on one "
    r"task, rendered at $2200\times1480$---moves $0.04\%$ of pixels by more than $32/255$ "
    r"and $0.025\%$ by more than $64/255$, i.e.\ \emph{below} the claimed $1\%$.",
    ["2200", "1480", "0.04", "32/255", "0.025", "64/255"],
    r"\S\ref{sec:related} withdraws the sentence asserting that a wrong camera ``moves "
    r"more than $1\%$ of pixels by more than $64/255$'' and quotes only the $0.025\%$.  "
    r"The point of the withdrawal is that the \emph{claimed} separation was two orders of "
    r"magnitude and the \emph{measured} one is not, and that argument needs both "
    r"thresholds, not one.")

add("A05", r"$\le 9$ was true only of the printed plate",
    r"Max channel difference $11/255$ at the $2200\times1480$ render, $8/255$ at the "
    r"printed plate's $659\times472$; an earlier caption said $\le 9$, which is true only "
    r"of the plate.",
    ["11/255", "8/255", "659", "472", "true only of the plate"],
    r"Figure~\ref{fig:containment}'s caption now quotes $11/255$ alone.  The self-"
    r"correction matters because the two numbers come from two different renders of the "
    r"same scene, and a determinism bar stated against the smaller one is a different bar "
    r"from the same bar stated against the larger one.  This is ledger row (25)'s "
    r"``a number quoted from prose rather than from a record''.")

add("A06", r"The raster's own exceedance fraction, \texttt{frac\_gt\_1}",
    r"Archived repeat captures differ by up to $8$--$13$ channel levels and "
    r"\texttt{frac\_gt\_1} reaches $8.3\times10^{-3}$.",
    ["frac", "8.3", "10^{-3}"],
    r"Appendix~\ref{sec:figures} reports the ``more than $\pm1$ LSB'' finding as a max "
    r"channel difference only.  The fraction is what makes $8$--$13$ levels benign rather "
    r"than alarming: it is the fraction of pixels differing by more than one level, and at "
    r"$8.3\times10^{-3}$ it says the difference is spread thin.  A max with no fraction "
    r"cannot distinguish antialiasing from a moved object.")

add("A07", "The separation the bar is actually shown to achieve",
    r"What the bar is actually shown to do is separate two captures of one scene (max "
    r"channel difference $8$--$13$, at most $10^{-4}$ of pixels above $8/255$) from two "
    r"different designs (max $188$, $4.0\times10^{-4}$ of pixels above $32/255$), which "
    r"is a factor of $14$ in channel difference and $4$ in pixel fraction, not two orders "
    r"of magnitude.",
    ["188", "4.0", "10^{-4}", "32/255", "14", "pixel fraction"],
    r"\S\ref{sec:related} keeps the $14\times$ and drops the $4\times$.  Reporting only "
    r"the larger of two factors measuring one separation is the same move as reporting "
    r"only the favourable noise floor, which \S\ref{sec:search} explicitly refuses to do "
    r"(``quoting only the favourable one is how a $5\times$ bar becomes decorative'').")

add("A08", "The compiled-project size triple behind ``273--318\\,KB''",
    r"Measured over the $2213$ compiled projects on disk: minimum $272.9$\,KB, median "
    r"$287.0$, maximum $317.4$.",
    ["2213", "272.9", "287.0", "317.4"],
    r"\S\ref{sec:intro} quotes the rounded range 273--318\,KB and the count.  The triple "
    r"is the evidence, and the review that found the loss re-measured it independently: "
    r"$2213$ files exactly ($2165$ under its scratch directory plus $48$ in the "
    r"repository), and over the $1797$ non-stub projects a minimum of $272.8877$\,KB, "
    r"median $287.18$, maximum $317.3828$, with $368$ sub-$100$\,KB stubs excluded.  An "
    r"earlier draft said ``90--320\,KB'', taking the lower bound from plan prose.")

add("A09", "The critical value the $\\lambda$ derivation hard-codes",
    r"The derivation script hard-codes $z = 1.96$, justified by $t_{0.975}$ at $574$ "
    r"residual df being $1.9641$---sound for residual df, unsound for a cluster-robust SE "
    r"with $G = 8$ clusters, where the usual reference is $t(7) = 2.3646$.",
    ["1.9641", "574", "2.3646"],
    r"Appendix~\ref{app:derivations} now says only that the script ``hard-codes "
    r"$z = 1.96$''.  The whole finding is that this choice is doing the work---at "
    r"$t(7)$ the pooled inverse cluster interval \emph{contains} $133.7$---and the "
    r"reader cannot assess that without knowing what $1.96$ was justified by and why the "
    r"justification does not transfer to $8$ clusters.")

add("A11", "The two-sample caveat every floor ratio inherits",
    r"These are ranges, not plus-minus values, and the uncontained ones rest on $n = 2$, "
    r"so every ratio below that divides by $0.043$\,K or $0.0168$ divides by a two-sample "
    r"range from that room.",
    ["n = 2", "0.043", "0.0168", "two-sample"],
    r"\S\ref{sec:env} states the two noise floors ($0.043$\,K and $0.0168$ for the "
    r"uncontained room) and \S\ref{sec:search} divides six measured effects by them.  "
    r"That $n = 2$ is the sample size behind a denominator used in every significance "
    r"claim in the paper is the single most load-bearing caveat in it, and the "
    r"compression deleted the sentence that says so.")

add("A12", "Why $\\lambda$ was pre-registered rather than fitted",
    r"$\lambda = 133.7$ is the slope ratio $184/1.376$ from the six-design pilot of "
    r"\S\ref{sec:tradeoff}, fixed before this run after a pilot showed $\lambda = 1$ made "
    r"the air oracle irrelevant.",
    ["184/1.376", "lambda = 1", "air oracle irrelevant"],
    r"Appendix~\ref{app:derivations} says $\lambda$ was ``fixed before this run'' without "
    r"saying what the alternative was.  The pilot is the reason the weight is a "
    r"pre-registration at all: at $\lambda = 1$ the liquid term dominates so completely "
    r"that the air objective---the expensive one, the one this paper exists to "
    r"differentiate---does not affect the ranking.  Pre-registering $\lambda$ is what "
    r"makes the $\lambda$-sweep in \S\ref{sec:search} descriptive rather than a search.")

add("A13", "The evidence that three decision blocks are constant",
    r"\textsc{orient}, \textsc{acu} and \textsc{contain} are \emph{constant} in every "
    r"search record in this campaign (all-zero orientation and containment indices, one "
    r"ACU temperature), so of the seven blocks the environment defines, exactly two have "
    r"ever been varied by a search.",
    ["all-zero orientation", "one ACU temperature", "seven blocks", "exactly two"],
    r"\S\ref{sec:search}'s limit (b) is the paper's most consequential scope restriction: "
    r"the environment defines seven decision blocks and the search has only ever moved "
    r"two.  The parenthetical is what makes ``constant'' a measurement rather than an "
    r"impression---it names the fields read out of the records and the values found.")

add("A14", "The de-biasing was not itself an optimisation",
    r"The result was not an artifact of minimising over noise.",
    ["not an artifact of minimising over noise"],
    r"The de-biasing recovered each arm's \emph{selected} design from its archived "
    r"compiled project rather than replaying the search, and evaluated each three more "
    r"times.  The minimum-over-$24$ selection bias is therefore removed by construction: "
    r"no arm gets to re-choose a design against the fresh draws.  Without this sentence a "
    r"reader cannot tell that re-measurement from a re-run, and a re-run \emph{would} be "
    r"an artifact of minimising over noise.")

add("A15", "Defect (9): the gate that passed over degenerate labels",
    r"The cheap oracle's $111\times$ all-air minimum, which the dataset generator "
    r"implemented in neither half, so it exited $0$ over $48$ identical degenerate "
    r"labels.",
    ["111", "exited", "48", "identical degenerate labels"],
    r"Ledger row (9) and \S\ref{sec:lessons} both say the generator implemented neither "
    r"half of the pre-registered action-space restriction, but neither says what the gate "
    r"did about it: it exited $0$.  ``48 identical degenerate labels'' is the count, and "
    r"it is the whole dataset.  This is the campaign's ``a check whose denominator can be "
    r"zero passed on zero'' pattern in its clearest instance.")

add("A16", "Defect (22) was reproduced, not asserted",
    r"Reproduced from synthetic records by the review that found it.",
    ["Reproduced from synthetic records by the review"],
    r"The vacuous pass---$24$ strictly-winning records plus $6$ empty ones scoring "
    r"$24/24$, $p = 0.0$, gate passed---was reproduced from synthetic records before it "
    r"was fixed, and the fix was then verified against the pre-fix scorer, which returns "
    r"the vacuous pass identically with and without \texttt{--allow-unequal}.  A defect "
    r"that is only argued is not a defect that was found.")

add("A17", "The room shell was never made translucent",
    r"The render tool is passed \texttt{--shell glass}, but every archived capture records "
    r"\texttt{scene.shellTouched = 0}, because the regex that selects shell meshes "
    r"(\texttt{Room\_top}, \texttt{Ground\_floor}, \texttt{floor}, \texttt{ceiling}, "
    r"\texttt{Raised}) matched no mesh in these projects, so no material was ever set to "
    r"opacity $0.12$.",
    ["shellTouched = 0", "Room", "Ground", "floor", "ceiling", "Raised", "0.12"],
    r"Every 3-D figure in this paper comes from the simulator's own web frontend, so what "
    r"the renderer does and does not draw is a property of the evidence.  "
    r"\S\ref{sec:related} says only ``the shell was never actually made translucent''; "
    r"the mechanism---a five-alternative regex matching no mesh, and the flag that was "
    r"passed but never took effect---is what makes that a diagnosis rather than an "
    r"observation, and it is why the frontend's blindness to containment baffles (defect "
    r"(11)) was not caught by looking at a plate.")

# A10 is not restored: it is already back in the paper by regeneration.
COVERED = [dict(
    id="A10", what=r"the bootstrap median of $\lambda$'s own interval, $129.7$",
    where="gen_appendix.tex", token="129.7",
    note=r"Lost from main.tex by the compression and reported lost from the generated "
         r"appendix too, at the revision under review.  It is back: "
         r"\texttt{fill\_results.py} regenerates the $\lambda$-conditioning table from "
         r"the record on every build, and that table now carries the "
         r"\texttt{bootstrap\_median} row.  Restoring it by hand here would create two "
         r"copies of one number, and the generated one is the one that cannot go stale.")]

# ---------------------------------------------------------------- provenance.
def old_source() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "show",
                        f"{OLD_REV}:docs/manuscripts/paper3_icml/main.tex"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot read {OLD_REV}: {r.stderr[:300]}")
    return r.stdout

def parse_args():
    """`--out` is required and may not point inside the manuscript directory.

    It was a bare `sys.argv[1]`, so `build_archive.py --help` did not print help: it created
    a directory literally named `--help` INSIDE the tracked paper folder and wrote
    `archive_section.tex` and `measurement_archive.json` into it, which then sat beside the
    sources as untracked files and had to be removed by hand.  Refusing the paper directory
    is the same lesson as `check_appendix_fresh.py`'s sandbox: a generator that can write
    into the tree it is checking will, eventually, by accident.
    """
    ap = argparse.ArgumentParser(
        description="Build the measurement archive section and its manifest from one source "
                    f"of truth, every number provenance-checked against {OLD_REV}.")
    ap.add_argument("--out", required=True, type=Path,
                    help="directory to write archive_section.tex and "
                         "measurement_archive.json into; must be OUTSIDE "
                         "docs/manuscripts/paper3_icml")
    a = ap.parse_args()
    out = a.out.resolve()
    if out == PAPER or PAPER in out.parents:
        ap.error(f"--out {out} is inside the tracked manuscript directory {PAPER}, so a "
                 f"mistyped flag would write generated files beside the sources; use a "
                 f"scratch directory and copy the manifest into records/ deliberately")
    a.out = out
    return a


def main() -> int:
    args = parse_args()
    old_src = old_source()
    old = old_src.splitlines()
    old_canon = canon(strip_tex(old_src))
    fails = []
    manifest = dict(
        old_revision=OLD_REV,
        old_path="docs/manuscripts/paper3_icml/main.tex",
        note=("Measurements present in the pre-compression revision and absent from both "
              "main.tex and the generated appendix afterwards.  Each entry's tokens are "
              "verified against the old revision at the recorded line numbers, so this "
              "appendix cannot introduce a number that was not already measured; and "
              "against the built PDF, so it cannot be silently truncated."),
        restored=[], covered_elsewhere=COVERED)
    items = []
    for e in E:
        lines = {}
        for t in e["tokens"]:
            hits = [i + 1 for i, ln in enumerate(old) if t in ln]
            if not hits:
                fails.append(f"{e['id']}: token {t!r} not found anywhere in {OLD_REV}")
                continue
            lines[t] = hits
        if len(lines) != len(e["tokens"]):
            continue
        # Sentence-level provenance, not token-level.  A short token like "4" or "1480"
        # appears on many lines of a 1050-line file, so pinning it proves little; and
        # pinning only the LONGEST prose run leaves the rest of the entry free to be
        # reworded -- a poison changing "the one failing design" to "the single failing
        # design" passed a longest-run-only check.  So every prose run of at least 20
        # canonical characters must be present in the pre-compression revision, which
        # makes a paraphrase that reuses the right digits impossible to manifest.
        runs = prose_runs(e["tex"])
        # Bounded, because check_measurement_archive.py deletes long digit runs out of the
        # extracted PDF text as page furniture and then looks for this probe in what is left.
        # probe() can emit such a run now that inline math is transparent and canon() glues
        # digits across the `\times` it deletes, and this value is written into a TRACKED
        # manifest -- an unusable probe here is a gate failure nobody can fix by rebuilding.
        pr = probe(e["tex"], max_digit_run=PROBE_DIGIT_RUN_LIMIT)
        if len(pr) < 24:
            fails.append(f"{e['id']}: no prose run >= 24 canonical characters to pin "
                         f"(probe {pr!r}); the entry is nearly all math")
            continue
        bad = [r for r in runs if r not in old_canon]
        if bad:
            fails.append(f"{e['id']}: {len(bad)} of {len(runs)} prose runs are NOT "
                         f"verbatim from {OLD_REV}: {bad[0][:48]!r} -- rewrite the entry "
                         f"from the old text rather than paraphrasing it")
            continue
        manifest["restored"].append(dict(
            id=e["id"], title=re.sub(r"\\[a-zA-Z]+|\{|}", "", e["title"]).strip(),
            why=e["why"], probe_old=pr, probe_tex=pr,
            prose_runs=runs,
            n_prose_runs=len(runs), old_lines=lines))
        items.append(
            "\\item \\textbf{(%s)} \\emph{%s}  %s  %s\n"
            % (e["id"], e["title"], e["tex"],
               "\\emph{Why it matters:} " + e["why"]))
    if fails:
        print("BUILD ARCHIVE FAILED: an entry is not verbatim from the pre-compression "
              "revision")
        for f in fails:
            print("  -", f)
        return 1

    tex = r"""\section{Measurement archive: what compressing the body removed}
\label{app:archive}
The body of this paper was compressed to ICML's eight-page limit and that compression
is what removed these measurements.  The draft now targets a nine-page limit
(Amendment~27), but the compression is not undone here: re-expanding prose by hand is the
same operation that deleted it, so what was lost is restored instead.  A cut made by deleting a clause range rather than by rewriting the
sentence deletes the measurements inside it, and nothing in the build says so: a review
that diffed the compressed revision against the pre-compression one
(\texttt{%s}) found \textbf{seventeen} measurements present there and absent from both
\texttt{main.tex} and the generated appendix afterwards, eight of them contradicting that
revision's own commit message, ``compressed, without dropping a measurement''.  None
changed a verdict.  All were evidence for one, and three---(A01), (A02) and (A15)---were
the only derivation of a constant the body still quotes, so their loss left a number in
the paper with no stated origin.  They are restored here, each with what it is evidence
for.

Two properties make this a restoration rather than a fresh set of typed numbers, which is
the failure mode \S\ref{sec:lessons} names twice.  First, \texttt{scripts/check\_measurement\_archive.py} reads \texttt{records/measurement\_archive.json} and verifies
every token below against the pre-compression revision \emph{at the line numbers recorded
in the manifest}, so a number enters this appendix only if \texttt{git show} finds it
there already.  Second, the same script verifies a distinctive prose probe from every
item against the \emph{built} PDF, because silent deletion is this campaign's recurring
disease: it happened twice by slicing a clause range and once by LaTeX dropping the
overflow of a \texttt{table*} while the build reported zero errors, zero overfull boxes
and zero undefined references.  A manifest with no entries fails rather than passing on an
empty denominator.  Sixteen entries are restored below; the seventeenth, (A10), came back
by regeneration and is listed after them with the reason it is not duplicated here.

This section is \emph{generated}: \texttt{scripts/}\allowbreak\texttt{build\_archive.py} holds the entries
and emits both the \LaTeX{} below and the manifest, and the same gate re-runs the builder
into a scratch directory and diffs both against what is committed.  That is the lesson of
ledger row (27), which is about an appendix that was a committed cached copy of a
generator's output and twice shipped stale in the direction that hid a result.  Editing
this section by hand therefore does not survive the build; edit the builder.

\begin{itemize}\itemsep1.5pt
%s\end{itemize}

\paragraph{Restored by regeneration instead.}
\textbf{(%s)} %s  It is in \texttt{%s}, which \texttt{fill\_results.py} regenerates from
the record on every build.  %s

""" % (OLD_REV, "".join(items), COVERED[0]["id"], COVERED[0]["what"],
       COVERED[0]["where"].replace("_", r"\_"), COVERED[0]["note"])

    # `_` is a subscript in LaTeX, so an unescaped one outside math mode fails the build
    # with "Missing $ inserted" -- which is how this section first built, on
    # \texttt{gen_appendix.tex}.  Math spans are removed before scanning, because
    # $t_{0.975}$ is a legitimate subscript and a lint that flags it trains whoever runs
    # it to ignore the output.
    bad = []
    for k, ln in enumerate(tex.splitlines()):
        nomath = re.sub(r"\$[^$]*\$", "", ln)
        if re.search(r"(?<!\\)_", nomath):
            bad.append(k + 1)
    if bad:
        raise SystemExit(f"BUILD ARCHIVE FAILED: unescaped underscore on emitted .tex "
                         f"line(s) {bad} -- LaTeX reads `_` as a subscript and fails with "
                         f"'Missing $ inserted', which is how this section first built")

    out = args.out; out.mkdir(parents=True, exist_ok=True)
    (out / "archive_section.tex").write_text(tex, encoding="utf-8")
    (out / "measurement_archive.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"BUILD ARCHIVE OK: {len(E)} entries restored + {len(COVERED)} covered "
          f"elsewhere")
    print(f"  every token AND a >=24-character prose probe of every entry found in "
          f"{OLD_REV}: this is a restoration, not new typing")
    print(f"  {sum(e['n_prose_runs'] for e in manifest['restored'])} prose runs pinned "
          f"across {len(manifest['restored'])} entries")
    print(f"  {tex.count(chr(10))} lines of .tex, "
          f"{sum(len(v) for e in manifest['restored'] for v in e['old_lines'].values())} "
          f"provenance line references")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
