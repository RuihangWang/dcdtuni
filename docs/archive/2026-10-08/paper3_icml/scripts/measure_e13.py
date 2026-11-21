#!/usr/bin/env python3
"""E13's published numbers: derived from the record, and checked against the manuscript.

The table this backs states Spearman rho and sign agreement for three decision blocks.
Every one of those values is in `results_e13.json`, produced by
`ddl/case/benchC/E13_fullrank_ranking/run.py` from complete single-edit neighbourhoods of
eight held-out tasks.  This script does two things:

  --emit   print the LaTeX rows, so the table can be pasted from output rather than typed;
  --check  parse the rows back out of main.tex and fail on any disagreement with the record.

Record resolution is TRACKED-FIRST, the same order `measure_e14.py` uses, because the
opposite order made this gate machine-dependent: `results_e13.json` and
`reanalysis_degeneracy.json` both live under gitignored `loggers/`, so on a clean clone
`--check` exited 2 with "no results_e13.json found" and every E13 number in the manuscript
went unchecked while the rest of the suite stayed green.  Both now have a committed copy --
`records/e13_ranking.json` for the table and
`ddl/case/benchC/E13_fullrank_ranking/tracked/reanalysis_degeneracy.json` for the
degeneracy numbers -- and when the live archive is ALSO present the two are compared, so a
tracked copy that has gone stale is a failure rather than a quiet divergence.

`--check` is the part that matters.  A caption that says "no number here is typed" is
only true if something compares the typeset number with the record, and this campaign has
found five numbers quoted from prose that were wrong every time anyone looked (ledger rows
12, 21, 22 and the "8 of 8 optimizer steps", the 85.5/54.2 GB pair and the 90 KB lower
bound).  The comparison is on the rounded values the table displays, to three decimals,
because that is the precision a reader can check.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
BLOCKS = [("diam", "diameter"), ("place", "placement"), ("assign", "assignment")]

# Tracked copies, tried FIRST.  Their live originals are gitignored, so a checkout that
# could only read the live ones could not check the manuscript at all.
TRACKED_SUMMARY = HERE / "records" / "e13_ranking.json"
TRACKED_DEGENERACY = (REPO / "ddl" / "case" / "benchC" / "E13_fullrank_ranking"
                      / "tracked" / "reanalysis_degeneracy.json")
LIVE_SUMMARY_GLOB = "ddl/case/benchC/E13_fullrank_ranking/loggers/*/results_e13.json"
LIVE_DEGENERACY_GLOB = ("ddl/case/benchC/E13_fullrank_ranking/loggers/"
                        "reanalysis_degeneracy.json")
_MISMATCH = object()


def resolve_pair(tracked: Path, live_glob: str, what: str, normalise=None):
    """(record_dict | None, provenance, problems) preferring the TRACKED copy.

    Reads the tracked copy when it exists, the live gitignored archive when it does not,
    and cross-checks the two when both exist.  Cross-checking is what stops the tracked
    copy becoming a mirror nobody compares: a campaign that moves the live record must
    move the tracked one in the same commit or this fails.  `normalise` maps either side
    onto the common shape before the comparison, because the tracked summary is a
    projection of the live record rather than a copy of it.
    """
    problems = []
    live = sorted(REPO.glob(live_glob))
    live = live[-1] if live else None
    if tracked.exists():
        try:
            d = json.loads(tracked.read_text())
        except Exception as e:
            return None, str(tracked), [f"{tracked.name} does not parse: {e}"]
        prov = str(tracked.relative_to(REPO))
        if live is not None:
            try:
                ld = json.loads(live.read_text())
            except Exception as e:
                problems.append(f"the live {what} at {live} does not parse: {e}")
                ld = None
            if ld is not None:
                a = normalise(d) if normalise else d
                b = normalise(ld) if normalise else ld
                if a != b:
                    ld = _MISMATCH
            if ld is _MISMATCH:
                problems.append(
                    f"the tracked {what} ({prov}) disagrees with the live archive "
                    f"({live.relative_to(REPO)}), so the copy the manuscript is checked "
                    f"against is stale; re-run the producer and commit both")
        return d, prov, problems
    if live is not None:
        return (json.loads(live.read_text()),
                f"{live.relative_to(REPO)} (gitignored, NOT tracked: a clean clone "
                f"cannot check this)", [])
    return None, None, [
        f"no {what}: neither the tracked copy at {tracked.relative_to(REPO)} nor the "
        f"gitignored archive matching {live_glob} is present, so every {what} number the "
        f"manuscript states is unchecked.  Restore the tracked copy or re-run the "
        f"producer; a gate that cannot see its evidence must not report OK."]


def resolve_values(args_record):
    """(vals, provenance, problems) for the E13 table."""
    if args_record:
        rec = Path(args_record)
        if not rec.exists():
            return None, None, [f"--record {rec} does not exist"]
        return load(rec), str(rec), []
    d, prov, problems = resolve_pair(TRACKED_SUMMARY, LIVE_SUMMARY_GLOB,
                                     "E13 ranking record (results_e13.json)",
                                     normalise=to_vals)
    if d is None:
        return None, None, problems
    return to_vals(d), prov, problems


def to_vals(d: dict) -> dict:
    """Either record shape -> the vals dict the table is checked against.

    Discriminate on `scored`, NOT on `blocks`: the live `results_e13.json` carries its own
    `blocks` key (the per-block neighbourhoods) as well as `scored`, so `"blocks" in d`
    picked the summary branch for the full record and the cross-check compared the tracked
    projection against a completely different structure.  It reported "the tracked copy is
    stale" on the author's machine, where both files exist, and nothing on a clean clone,
    where only the tracked one does -- a finding that appears on exactly one machine is the
    defect this whole file was rewritten to remove.
    """
    if "scored" in d:
        return load_from_full(d)
    if "blocks" in d:
        return d["blocks"]
    raise SystemExit(f"record has neither `scored` nor `blocks`: {sorted(d)[:8]}")


def load_from_full(d: dict) -> dict:
    """`load()`'s projection, applied to an already-parsed full record."""
    out = {}
    for key, _label in BLOCKS:
        sc = d["scored"][key]["relaxed"]
        out[key] = {"n_edits": sc["n_edits"],
                    "rho_median": sc["rho_median_over_tasks"],
                    "rho_min": sc["rho_min"],
                    "sign": sc["sign_agreement_pooled"],
                    "n_tasks_rho_ge_0.9": sc["n_tasks_rho_ge_0.9"],
                    "n_tasks": sc["n_tasks"]}
        hard = d["scored"][key].get("hard")
        if hard:
            out[key]["rho_median_hard"] = hard["rho_median_over_tasks"]
    out["_gates"] = d["gates"]
    return out


# (record path within the reanalysis, value the manuscript must state, to 3 decimals)
DEGENERACY = [
    ("blocks|assign/relaxed", "rho_informative", "median",
     "-0.193", "the informative-half rho on assignment"),
    ("blocks|assign/relaxed", "n_degenerate", None, "112",
     "the number of structural no-ops in the assignment neighbourhood"),
    ("blocks|assign/relaxed", "n_edits", None, "224", "the assignment neighbourhood size"),
    ("constant_rule|assign/relaxed", "rho_informative", "median", "+0.431",
     "the gradient-free constant rule on assignment's informative half"),
    ("constant_rule|diam/relaxed", "rho_informative", "median", "+0.867",
     "the gradient-free constant rule on diameter"),
]


def load(record: Path) -> dict:
    return load_from_full(json.loads(record.read_text()))


def emit(vals: dict) -> str:
    rows = []
    for key, label in BLOCKS:
        v = vals[key]
        note = {"diam": "affine in $1/D^5$", "place": "affine in length",
                "assign": "inside the solve"}[key]
        bold = (lambda x: f"$\\mathbf{{{x:.3f}}}$") if v["rho_median"] < 0.5 else (lambda x: f"${x:.3f}$")
        rows.append(f"{label} ({note})   & {v['n_edits']} & {bold(v['rho_median'])} & "
                    f"${v['rho_min']:.3f}$ & "
                    + (f"$\\mathbf{{{v['sign']:.3f}}}$" if v["sign"] < 0.9 else f"${v['sign']:.3f}$")
                    + r" \\")
    return "\n".join(rows)


def dig(rec: dict, path: str):
    """Walk a record by "|"-separated keys.

    Not "/": the reanalysis record's keys ARE "assign/relaxed", one string with a slash in
    it, so splitting on "/" walked into a level that does not exist and reported every
    field missing.
    """
    cur = rec
    for k in path.split("|"):
        if isinstance(cur, list):
            cur = cur[int(k)]
        else:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def check_degeneracy(tex: Path, problems: list) -> str:
    """Compare the four degeneracy numbers against the reanalysis record.

    Returns a note naming the record the numbers were checked against, or None when there
    was no record to check them against -- which is a FAILURE, recorded in `problems`.
    """
    src = re.sub(r"\s+", " ", tex.read_text(encoding="utf-8"))
    rec, prov, why = resolve_pair(TRACKED_DEGENERACY, LIVE_DEGENERACY_GLOB,
                                  "E13 degeneracy reanalysis record "
                                  "(reanalysis_degeneracy.json)")
    problems.extend(why)
    if rec is None:
        # Was a note and an exit 0, while measure_e14.py returns 2 for the same condition.
        # Two gates written in the same round had opposite failure modes and the permissive
        # one guarded the newer claim: on a clean clone the four degeneracy numbers that
        # answer round 3 went unchecked and the suite still said OK.  Absence of evidence
        # for a headline claim is a failure, not a skip.
        return None
    problems.extend(check_hard_inertness(rec, src))
    problems.extend(check_claim_scoped(rec, src))
    if not rec.get("self_check", {}).get("all_published_values_reproduced"):
        problems.append("the reanalysis record does not reproduce the published rho "
                        "bitwise, so its informative-half numbers are not comparable with "
                        "the published ones and nothing it says may be quoted")
    for path, field, sub_field, want, why in DEGENERACY:
        node = dig(rec, path)
        if node is None:
            problems.append(f"{path} is missing from the reanalysis record ({why})")
            continue
        got = node.get(field) if isinstance(node, dict) else None
        if isinstance(got, dict) and sub_field:
            got = got.get(sub_field)
        if got is None:
            problems.append(f"{path}.{field} is missing ({why})")
            continue
        rendered = (f"{got:+.3f}" if isinstance(got, float) and abs(got) < 10
                    else str(int(got)))
        if want not in src:
            problems.append(f"main.tex does not state {want} ({why}); the reanalysis "
                            f"record says {rendered}")
        elif rendered.lstrip("+") not in src.replace("+", "").replace("$", ""):
            problems.append(f"main.tex states {want} for {why} but the record now says "
                            f"{rendered}, so the typeset value has gone stale")
    if not rec.get("hard_vs_relaxed", {}).get("assign", {}).get(
            "rho_identical_on_every_task"):
        problems.append("the reanalysis no longer finds the assignment block's hard rho "
                        "identical to its relaxed one, but the manuscript still calls it "
                        "one measurement rather than two")
    elif "bitwise identical" not in src:
        problems.append("the assignment block's hard rho is bitwise identical to its "
                        "relaxed one on every task and main.tex does not say so, which "
                        "leaves a reader taking two columns for two measurements")
    return f"degeneracy numbers checked against {prov}"


def claim_scoped(src, specs, problems, where):
    """Every match of each anchored pattern must equal the value the record gives.

    specs: (pattern, expected, label).  `expected` may be a string, a number, a tuple (for a
    pattern with several groups) or a set of acceptable strings.  Zero matches is a failure:
    an anchored pattern that stops matching is a gate that has gone dead, and the prose
    moving is the usual reason.
    """
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for "
                            f"{label}, so that number is unchecked -- the prose moved or the "
                            "anchor is wrong, and a check with nothing to check is not a pass")
            continue
        if isinstance(expected, tuple):
            want = tuple(str(e) for e in expected)
            bad = [h for h in hits if tuple(h) != want]
            shown = " / ".join(want)
        else:
            ok = ({str(expected)} if isinstance(expected, (str, int, float))
                  else {str(e) for e in expected})
            flat = [h if isinstance(h, str) else " / ".join(h) for h in hits]
            bad = [h for h in flat if h not in ok]
            shown = ", ".join(sorted(ok)[:4])
        if bad:
            problems.append(f"{where}: {len(bad)} of {len(hits)} statements of {label} "
                            f"disagree with the record: {sorted(set(map(str, bad)))[:4]} "
                            f"against {shown}")
        else:
            print(f"  {where}: {label} -- all {len(hits)} statement(s) agree with the record")


def check_claim_scoped(rec, src):
    """The load-bearing E13 correlations, each matched where it is stated."""
    problems = []
    b = rec.get("blocks") or {}
    c = rec.get("constant_rule") or {}

    def med(d, *path):
        v = d
        for k in path:
            v = (v or {}).get(k)
        return None if v is None else f"{float(v):.3f}".replace("-", "-")

    assign_info = med(b.get("assign/relaxed"), "rho_informative", "median")
    diam_info = med(b.get("diam/relaxed"), "rho_all_edits", "median")
    const_assign = med(c.get("assign/relaxed"), "rho_informative", "median")
    const_diam = med(c.get("diam/relaxed"), "rho_all_edits", "median")
    hard_diam = med(c.get("diam/hard"), "rho_informative", "median")
    hard_all_diam = med(c.get("diam/hard"), "rho_all_edits", "median")
    cap = [(med(b.get(f"{k}/hard"), "rho_all_edits", "median")) for k in ("diam", "place", "assign")]

    specs = [
        (r"informative half gives \$\\rho = (-?[\d.]+)\$", assign_info,
         "the informative-half rho on assignment"),
        (r"constant index rule's \$\+?(-?[\d.]+)\$", const_assign,
         "the constant rule's rho on assignment's informative half"),
        (r"constant rule above already reaches \$\+?(-?[\d.]+)\$", const_diam,
         "the constant rule's rho on diameter"),
        (r"exact on pipe diameter \(\$\\rho = (-?[\d.]+)\$\)", diam_info,
         "the adjoint's rho on diameter"),
        # the caption's three hard-network values, which nothing checked before
        (r"Against the hard one the substance is unchanged \(diameter \$(-?[\d.]+)\$, "
         r"placement\s*\$(-?[\d.]+)\$, assignment \$(-?[\d.]+)\$\)", tuple(cap),
         "the tab:e13 caption's three hard-network rho values"),
        # the hard-informative diameter half, quoted in the new appendix paragraph
        (r"hard-informative diameter edits\s*\$\\rho = (-?[\d.]+)\$",
         med(b.get("diam/hard"), "rho_informative", "median"),
         "the adjoint's rho on the hard-informative diameter half"),
        (r"constant rule's \$\+(-?[\d.]+)\$ on the same\s*subset", hard_diam,
         "the constant rule on the hard-informative diameter half"),
        (r"\$\+(-?[\d.]+)\$ over all \$223\$", hard_all_diam,
         "the constant rule over all diameter edits against the hard objective"),
    ]
    claim_scoped(src, [(p, e, l) for p, e, l in specs if e is not None
                       and (not isinstance(e, tuple) or all(x is not None for x in e))],
                problems, "e13")
    dropped = [l for p, e, l in specs
               if e is None or (isinstance(e, tuple) and any(x is None for x in e))]
    if dropped:
        problems.append("e13: the reanalysis record is missing fields needed to check "
                        f"{dropped}")
    return problems


def check_hard_inertness(rec, src):
    """The inertness of each block against BOTH objectives, from the reanalysis record.

    E13 calls an edit degenerate when the prediction and the measured effect are both
    exactly zero, which on diameter finds none -- and reads as a fully informative block.
    Against the hard objective 111 of the 223 diameter edits change nothing at all while
    the relaxation reports an effect on every one, so the block is half inert physically
    and wholly sensitive in the surrogate.  Those are different statements and the
    manuscript now makes both, so both are checked.
    """
    problems = []
    blocks = rec.get("blocks") or {}
    const = rec.get("constant_rule") or {}
    want = []
    for key, label in (("diam/hard", "diameter hard-inert"),
                       ("diam/relaxed", "diameter relaxed-inert"),
                       ("place/hard", "placement hard-inert"),
                       ("place/relaxed", "placement relaxed-inert"),
                       ("assign/hard", "assignment hard-inert")):
        b = blocks.get(key)
        if not b:
            problems.append(f"the reanalysis record has no {key} block, so the "
                            f"{label} count in the appendix is unchecked")
            continue
        want.append((str(b["n_measured_zero"]), f"{label} edits ({key} n_measured_zero)"))
        want.append((str(b["n_edits"]), f"{label} block size ({key} n_edits)"))
    dh = (const.get("diam/hard") or {}).get("rho_informative") or {}
    da = (const.get("diam/hard") or {}).get("rho_all_edits") or {}
    adj = (blocks.get("diam/hard") or {}).get("rho_informative") or {}
    for val, why in ((dh.get("median"), "the constant rule on the hard-informative diameter half"),
                     (da.get("median"), "the constant rule over all diameter edits"),
                     (adj.get("median"), "the adjoint on the hard-informative diameter half")):
        if val is None:
            problems.append(f"{why} is absent from the record")
        else:
            want.append((f"{val:.3f}".rstrip("0").rstrip("."), why))
    for needle, why in want:
        if needle not in src:
            problems.append(f"main.tex does not contain {needle!r} ({why})")
    if not problems:
        print(f"  hard inertness: {len(want)} values agree with "
              f"reanalysis_degeneracy.json (diam {blocks['diam/hard']['n_measured_zero']}"
              f"/{blocks['diam/hard']['n_edits']} hard-inert against "
              f"{blocks['diam/relaxed']['n_measured_zero']} relaxed-inert)")
    return problems


def check(vals: dict, tex: Path, provenance_problems=(), record_prov="?") -> int:
    src = tex.read_text(encoding="utf-8")
    m = re.search(r"\\label\{tab:e13\}", src)
    if not m:
        print("no tab:e13 in main.tex", file=sys.stderr); return 2
    start = src.rindex(r"\begin{table}", 0, m.start())
    blk = src[start:m.end()]
    problems = list(provenance_problems)
    for key, label in BLOCKS:
        v = vals[key]
        row = next((l for l in blk.split("\n") if l.strip().startswith(label)), None)
        if row is None:
            problems.append(f"no table row for {label}")
            continue
        flat = re.findall(r"\d+\.\d+|\b\d+\b", row)
        got = [float(x) for x in flat]
        want = [v["n_edits"], v["rho_median"], v["rho_min"], v["sign"]]
        # the row may carry the label's own digits, so compare the LAST four numbers
        tail = got[-4:]
        if len(tail) != 4:
            problems.append(f"{label}: expected 4 numbers, parsed {len(tail)} from {row!r}")
            continue
        if int(round(tail[0])) != int(want[0]):
            problems.append(f"{label}: edits {int(round(tail[0]))} != record {want[0]}")
        for i, name in ((1, "rho_median"), (2, "rho_min"), (3, "sign")):
            if abs(tail[i] - round(want[i], 3)) > 5e-4:
                problems.append(f"{label}: {name} {tail[i]} != record {want[i]:.6f}")
    g = vals["_gates"]
    if not (src.count("fails on assignment") or src.count(r"\textbf{fails on assignment}")):
        problems.append("the table's caption must say the gate fails on assignment")
    if g["pass"]:
        problems.append("the record says the gate PASSED, but the manuscript reports a failure")
    note = check_degeneracy(tex, problems)
    print(f"E13 numbers checked against {tex.name}: {len(BLOCKS)} blocks; {note}")
    if problems:
        for x in problems:
            print(f"  - {x}", file=sys.stderr)
        return 1
    print(f"E13 NUMBERS OK (table and degeneracy numbers agree with their tracked "
          f"records: {record_prov})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default=None)
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--summary", default=None,
                    help="also write a small tracked summary beside the manuscript")
    args = ap.parse_args()

    vals, prov, problems = resolve_values(args.record)
    if vals is None:
        # Was `print("no results_e13.json found"); return 2` -- two words of stderr that
        # named neither the path it looked for nor the tracked copy that should have been
        # there, which is why a clean clone's finding read as a mystery.  Still exit 2
        # (distinct from 1: no evidence, rather than evidence that disagrees), but say
        # what to do about it.
        for x in problems:
            print(f"  - {x}", file=sys.stderr)
        print("measure_e13: cannot check E13's numbers without a record; nothing was "
              "compared, so this is NOT a pass", file=sys.stderr)
        return 2
    print(f"e13 record: {prov}")
    if args.emit:
        print(emit(vals))
    if args.summary:
        out = {"provenance": "ddl/case/benchC/E13_fullrank_ranking/run.py",
               "record": prov, "blocks": vals,
               "note": "the pre-registered gate FAILS on assignment and is published as "
                       "a failure; exactness of the adjoint is a separate claim from "
                       "usefulness of its first-order ranking"}
        Path(args.summary).write_text(json.dumps(out, indent=1, default=str))
        print(f"wrote {args.summary}", file=sys.stderr)
    if args.check:
        return check(vals, Path(args.tex), problems, prov)
    for x in problems:
        print(f"  - {x}", file=sys.stderr)
    if not args.emit:
        print(json.dumps(vals, indent=1, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
