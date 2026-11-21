#!/usr/bin/env python3
"""Gate the V0 validity audit's numbers, and its DUAL verdict, against two records that agree.

`records/e12_v0_audit.json` is `v0_audit.py` run over the two completed E12 arms.  A round-7 review
scanned all 18 tracked records and found this was the only one with no reader in `scripts/`, so the
five numbers the body prints about V0 -- 15 of 200 steps, 7.5%, 185 effective steps, the 65
degenerate-gradient steps, and the step-1 rescale in [1e6, 1e11) -- were prose-carried against a
record nothing compared them with.  This file is that reader.

THREE JOBS, in the order they matter.

1. CROSS-RECORD AGREEMENT, which is the structural fix for the review's N8.  Two shipped records
   described the SAME `grpo_s0` log and disagreed: one said 63 of 197 steps, the other 65 of 200.
   Neither was a typo -- they count different populations -- but nothing in the repository forced
   the two to be reconciled, so the manuscript could quote either and every gate stayed green.
   `records/e12_agent_result.json` carries, per arm, `n_step_records` (200),
   `n_step_records_non_eval_payloads` (197), `sf_zero_step_count` (15) and
   `degenerate_step_count` (65).  Every one of those is compared with what
   `records/e12_v0_audit.json` says about the same file, and a disagreement names BOTH paths and
   BOTH values rather than reporting "the numbers differ".

2. RE-DERIVATION.  `frac_sf_zero`, `frac_task_grad_degenerate` and `steps_with_nonzero_gradient`
   are recomputed from the counts and compared with the stored values at the precision the body
   prints.  A stored fraction that no longer follows from its own counts is a record that was
   edited by hand.

3. THE DUAL READING.  Amendment 30.10 exists because a gate that reports only the reading under
   which it passed has chosen its own verdict: V0 returns `pass: true` under Amendment 29 while
   A11.6 as literally pre-registered -- `sf == 0` on 0% of logged epochs, zero tolerance -- is
   FAILED by the scalar-reward arm at 7.5%.  So this gate requires BOTH keys to be present for
   every arm (`pass` and `a11_6_literal.pass`, values included, `null` included), requires
   `guard_branch_has_no_upper_bound: true`, requires a non-empty `warnings` list on every arm whose
   literal reading fails, and requires the prose to carry both readings and to name the record.

Every numeric anchor is claim-scoped to the V0 paragraph, not to the document: a 33-page file has
enough stray integers that a document-wide search validates a number against an unrelated
occurrence of the same digits, which is how a tenfold error in this manuscript's abstract once
passed.  ZERO matches is a FAILURE, because an anchored pattern that stops matching is a gate that
has gone dead and the prose moving is the usual reason.

Usage: measure_v0.py [--check] [--print-values] [--record PATH] [--agent-record PATH] [--tex PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
AGENT_ARM_OF_V0_ARM = {"ste": "ste_s0", "grpo": "grpo_s0"}
# What identifies the paragraph that WRITES V0 UP, as opposed to the one that mentions it in
# passing.  Each is a regex over main.tex source.
V0_PARAGRAPH_ANCHORS = (
    r"records/e12\\_v0\\_audit\.json",     # the record, named where its numbers are quoted
    r"\\emph\{literally\}",                 # the A11.6 reading the pass is qualified by
    r"a11_6_literal",                           # the record's own dual-reading key
    r"effective steps",                         # the 185-of-200 consequence
    r"no upper bound",                          # the unbounded guard branch
)


def claim_scoped(src, specs, problems, where):
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
        if not hits:
            problems.append(f"{where}: nothing in main.tex matches the claim pattern for {label}, "
                            "so that number is unchecked -- the prose moved or the anchor is wrong, "
                            "and a check with nothing to check is not a pass")
            continue
        if isinstance(expected, tuple):
            want = tuple(str(e) for e in expected)
            bad = [h for h in hits if tuple(h) != want]
            shown = " / ".join(want)
        else:
            ok = {str(expected)}
            flat = [h if isinstance(h, str) else " / ".join(h) for h in hits]
            bad = [h for h in flat if h not in ok]
            shown = ", ".join(sorted(ok)[:4])
        if bad:
            problems.append(f"{where}: {len(bad)} of {len(hits)} statements of {label} disagree "
                            f"with the record: {sorted(set(map(str, bad)))[:4]} against {shown}")
        else:
            print(f"  {where}: {label} -- all {len(hits)} statement(s) agree with the record")


def paragraphs(src):
    """main.tex split on blank lines, with each paragraph's start offset."""
    out, start = [], 0
    for m in re.finditer(r"\n\s*\n", src):
        out.append((start, src[start:m.start()]))
        start = m.end()
    out.append((start, src[start:]))
    return out


def v0_paragraph(src):
    """(paragraph, n_anchors) for the paragraph that writes V0 up, or ("", 0).

    "validity audit V0" appears TWICE in this manuscript -- once in related work, where it is a
    passing mention, and once in the agent section, where the audit's verdict and its numbers
    are stated.  Selecting on that phrase alone picks the wrong one and every anchor then
    reports zero matches, which reads as "the prose moved" when the truth is "the gate looked in
    the wrong paragraph".  So the paragraph is chosen by whichever of several anchors it carries
    the most of, and the caller additionally requires it to mention the audit at all.
    """
    best, best_n = "", 0
    for _, para in paragraphs(src):
        n = sum(1 for a in V0_PARAGRAPH_ANCHORS if re.search(a, para, re.S))
        if n > best_n:
            best, best_n = para, n
    return best, best_n


def load_json(path: Path, label: str, problems):
    if not path.exists():
        problems.append(f"v0: no {label} at {path}; the V0 numbers in the body would be "
                        f"unchecked. Regenerate it rather than deleting this gate: 'the record "
                        f"is not here' and 'the record agrees' must not print the same thing")
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:                              # noqa: BLE001
        problems.append(f"v0: {label} at {path} does not parse: {e}")
        return None


def derive(v0arm, agentarm):
    """Everything this gate re-derives, from counts only.  `None` means not derivable."""
    n = v0arm.get("records")
    lit = v0arm.get("a11_6_literal") or {}
    sf_present = v0arm.get("sf_present")
    n_sf_zero = lit.get("n_sf_zero")
    n_guarded = v0arm.get("n_sf_zero_guarded")
    n_with_signal = len(v0arm.get("sf_zero_steps_with_signal") or [])
    degen = agentarm.get("degenerate_step_count") if agentarm else None
    out = {
        "n_sf_zero_from_parts": (n_guarded + n_with_signal
                                 if n_guarded is not None else None),
        "frac_sf_zero": (round(n_sf_zero / sf_present, 6)
                         if (n_sf_zero is not None and sf_present) else None),
        "frac_sf_zero_pct_1dp": (f"{100.0 * n_sf_zero / sf_present:.1f}"
                                 if (n_sf_zero is not None and sf_present) else None),
        "frac_task_grad_degenerate": (round(degen / n, 6)
                                      if (degen is not None and n) else None),
        "steps_with_nonzero_gradient_from_v0": (
            sf_present - n_guarded - n_with_signal
            if (sf_present and n_guarded is not None) else None),
        "steps_with_nonzero_gradient_from_agent": (
            agentarm["n_step_records"] - agentarm["sf_zero_step_count"]
            if agentarm and agentarm.get("n_step_records") is not None
            and agentarm.get("sf_zero_step_count") is not None else None),
    }
    return out


def cross_check(v0arm, agentarm, v0path, v0rel, arel, label, problems):
    """N8: two shipped records, one log.  Every shared quantity must agree, and a disagreement
    names both files and both values."""
    def disagree(what, v0val, agval, v0key, agkey):
        problems.append(f"v0: {what} DISAGREES between two shipped records of the SAME "
                        f"{label} log: {v0rel}['{v0key}'] = {v0val!r} while "
                        f"{arel}['{agkey}'] = {agval!r}. This is the N8 defect -- two records "
                        f"of one run that nothing reconciled, so the body could quote either")

    pairs = (
        ("the step population", v0arm.get("records"), agentarm.get("n_step_records"),
         "records", "n_step_records"),
        ("the score-function zero-step count", (v0arm.get("a11_6_literal") or {}).get("n_sf_zero"),
         agentarm.get("sf_zero_step_count"), "a11_6_literal.n_sf_zero", "sf_zero_step_count"),
        ("the expected step count", v0arm.get("expected_steps"),
         agentarm.get("steps_requested"), "expected_steps", "steps_requested"),
        ("the OOM event count", v0arm.get("oom_events"), agentarm.get("oom_total"),
         "oom_events", "oom_total"),
        ("the completed-step count", v0arm.get("steps_done"),
         (agentarm.get("done_record") or {}).get("steps_done"), "steps_done",
         "done_record.steps_done"),
        ("the run_invalid flag", v0arm.get("run_invalid"),
         (agentarm.get("done_record") or {}).get("run_invalid"), "run_invalid",
         "done_record.run_invalid"),
        ("the physics-evaluation count", (v0arm.get("physics") or {}).get("forward"),
         ((agentarm.get("done_record") or {}).get("physics") or {}).get("forward"),
         "physics.forward", "done_record.physics.forward"),
    )
    for what, a, b, ka, kb in pairs:
        if a is None and b is None:
            continue
        if a != b:
            disagree(what, a, b, ka, kb)
        else:
            print(f"  v0: {label}: {what} agrees across both records ({a!r} in "
                  f"{v0rel} and {arel})")

    # the degenerate-step count: v0 stores a FRACTION, the agent record stores a COUNT
    frac = v0arm.get("frac_task_grad_degenerate")
    n = v0arm.get("records")
    degen = agentarm.get("degenerate_step_count")
    if frac is not None and n and degen is not None:
        if round(frac * n) != degen:
            disagree("the degenerate-gradient step count", f"{frac} * {n} = {round(frac * n)}",
                     degen, "frac_task_grad_degenerate x records", "degenerate_step_count")
        else:
            print(f"  v0: {label}: the degenerate-gradient step count agrees across both "
                  f"records ({degen} of {n}; {v0rel} stores it as the fraction {frac})")

    # the sf-zero step LISTS, not only their lengths
    v0_steps = sorted(list(v0arm.get("sf_zero_steps_guarded") or [])
                      + [d.get("step") for d in (v0arm.get("sf_zero_steps_with_signal") or [])])
    ag_steps = sorted(agentarm.get("sf_zero_steps") or [])
    if v0_steps != ag_steps:
        disagree("the list of score-function zero steps", v0_steps[:8], ag_steps[:8],
                 "sf_zero_steps_guarded + sf_zero_steps_with_signal", "sf_zero_steps")
    elif ag_steps or v0_steps:
        print(f"  v0: {label}: the {len(ag_steps)} score-function zero step(s) are the same "
              f"steps in both records")


def population_guard(v0arm, agentarm, src, label, problems):
    """N8 was not a typo, it was two POPULATIONS.  Pin which one the prose uses.

    `n_step_records` is the distinct training steps 1..max, the population V0 audits and the one
    the body's `of $200$` refers to.  `n_step_records_non_eval_payloads` is the narrower
    record-level filter (197), and the review's "63 of 197" was that population applied to a
    different quantity altogether.  Both are kept and named in the agent record; this gate
    requires the prose denominator to be the one V0 actually audited over.
    """
    n_all = agentarm.get("n_step_records")
    n_non = agentarm.get("n_step_records_non_eval_payloads")
    if n_all is None or n_non is None:
        problems.append(f"v0: {label}: {AREL} carries no n_step_records / "
                        f"n_step_records_non_eval_payloads, so the population the prose "
                        f"denominator refers to cannot be pinned")
        return
    frac = v0arm.get("frac_task_grad_degenerate")
    degen = agentarm.get("degenerate_step_count")
    if frac is None or degen is None:
        return
    alt = round(frac * n_non)
    print(f"  v0: {label}: two populations are on record -- {n_all} distinct training steps "
          f"(the one V0 audited, giving {degen}) and {n_non} non-eval payloads (which would "
          f"give {alt}). The body's denominator is pinned to {n_all} below")
    if n_all == n_non:
        return
    m = re.findall(r"degenerate-gradient steps of \$(\d+)\$", src)
    bad = [d for d in m if d == str(n_non) and d != str(n_all)]
    if bad:
        problems.append(f"v0: {label}: main.tex states the degenerate-gradient count over "
                        f"${bad[0]}$, which is {AREL}'s n_step_records_non_eval_payloads, not "
                        f"the {n_all} distinct training steps V0 audited "
                        f"({V0REL}). The two populations give {degen} and {alt}; quoting the "
                        f"narrower one is the 63-of-197 reading the review mistook for this "
                        f"quantity")


AREL = "records/e12_agent_result.json"
V0REL = "records/e12_v0_audit.json"


def summarise(v0, agent, problems):
    """The values the prose must carry, per arm, plus the two records reconciled."""
    out = {"arms": {}}
    # v0_audit keys its arms by LOG PATH, so each arm is found by the `arm` field it carries.
    for v0arm_name, agent_name in AGENT_ARM_OF_V0_ARM.items():
        hits = {k: v for k, v in (v0.get("arms") or {}).items()
                if (v or {}).get("arm") == v0arm_name}
        if len(hits) != 1:
            problems.append(f"v0: {V0REL} carries {len(hits)} arm entries whose `arm` is "
                            f"{v0arm_name!r} ({sorted(hits)[:3]}); expected exactly one, so "
                            f"this gate cannot tell which log the body is quoting")
            continue
        v0path, v0arm = next(iter(hits.items()))
        agentarm = (agent.get("arms") or {}).get(agent_name) if agent else None
        if agentarm is None:
            problems.append(f"v0: {AREL} has no arms.{agent_name}, so the {v0arm_name} arm in "
                            f"{V0REL} has nothing to be reconciled against and the N8 defect "
                            f"(two records of one log disagreeing) is ungated")
        out["arms"][v0arm_name] = {
            "v0_path": v0path, "agent_arm": agent_name, "v0": v0arm,
            "agent": agentarm, "derived": derive(v0arm, agentarm or {})}
    out["V0_pass"] = v0.get("V0_pass")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--print-values", action="store_true",
                    help="print the exact strings the anchors expect, so the prose is written "
                         "FROM the record rather than next to it")
    ap.add_argument("--record", default=str(HERE / "records" / "e12_v0_audit.json"))
    ap.add_argument("--agent-record", default=str(HERE / "records" / "e12_agent_result.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp, ap_ = Path(args.record), Path(args.agent_record)
    problems: list[str] = []
    if not rp.exists():
        print("V0 GATE FAILED:", file=sys.stderr)
        print(f"  - FAIL: no V0 record at {rp}; the V0 numbers in the body would be unchecked. "
              f"Regenerate with ddl/case/benchC/E12c_policy_eval/v0_audit.py over the two arms. "
              f"An absent record is not a skip: 'the evidence is not here' and 'the evidence "
              f"agrees' must not print the same thing", file=sys.stderr)
        return 2
    v0 = load_json(rp, f"V0 record ({V0REL})", problems)
    agent = load_json(ap_, f"agent record ({AREL})", problems)
    if v0 is None or agent is None:
        print("V0 GATE FAILED:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    S = summarise(v0, agent, problems)
    if args.print_values:
        printable = {}
        for name, a in S["arms"].items():
            d, v0arm = a["derived"], a["v0"]
            printable[name] = {
                "records": v0arm.get("records"),
                "n_sf_zero": (v0arm.get("a11_6_literal") or {}).get("n_sf_zero"),
                "frac_sf_zero": v0arm.get("frac_sf_zero"),
                "sf_zero_pct_1dp": d["frac_sf_zero_pct_1dp"],
                "steps_with_nonzero_gradient": v0arm.get("steps_with_nonzero_gradient"),
                "frac_task_grad_degenerate": v0arm.get("frac_task_grad_degenerate"),
                "degenerate_step_count": (a["agent"] or {}).get("degenerate_step_count"),
                "a11_6_literal_pass": (v0arm.get("a11_6_literal") or {}).get("pass"),
                "pass": v0arm.get("pass"),
                "n_rescale_large_finite": v0arm.get("n_rescale_large_finite"),
                "rescale_large_finite_steps": v0arm.get("rescale_large_finite_steps"),
                "frac_rescale_saturated": v0arm.get("frac_rescale_saturated"),
                "n_step_records_non_eval_payloads":
                    (a["agent"] or {}).get("n_step_records_non_eval_payloads"),
            }
        print(json.dumps(printable, indent=1))
        return 0

    if problems:
        print("V0 GATE FAILED:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    # ---- 1/2. cross-record agreement and re-derivation, per arm -----------------
    for name, a in sorted(S["arms"].items()):
        v0arm, agentarm, d = a["v0"], a["agent"] or {}, a["derived"]
        if agentarm:
            cross_check(v0arm, agentarm, a["v0_path"], V0REL, AREL, name, problems)

        lit = v0arm.get("a11_6_literal") or {}
        sf_present = v0arm.get("sf_present")
        # re-derivation: the stored fractions must follow from the counts
        if sf_present:
            if v0arm.get("frac_sf_zero") != d["frac_sf_zero"]:
                problems.append(
                    f"v0: {name}: {V0REL} stores frac_sf_zero = "
                    f"{v0arm.get('frac_sf_zero')!r} but {lit.get('n_sf_zero')} of "
                    f"{sf_present} score-function-zero steps is {d['frac_sf_zero']!r}: the "
                    f"stored fraction no longer follows from the record's own counts")
            else:
                print(f"  v0: {name}: frac_sf_zero {d['frac_sf_zero']} re-derived from "
                      f"{lit.get('n_sf_zero')}/{sf_present}")
            if d["n_sf_zero_from_parts"] != lit.get("n_sf_zero"):
                problems.append(
                    f"v0: {name}: a11_6_literal.n_sf_zero = {lit.get('n_sf_zero')} but "
                    f"n_sf_zero_guarded ({v0arm.get('n_sf_zero_guarded')}) plus the "
                    f"{len(v0arm.get('sf_zero_steps_with_signal') or [])} step(s) with signal "
                    f"is {d['n_sf_zero_from_parts']}: the split does not sum to the total")
        else:
            if v0arm.get("frac_sf_zero") is not None:
                problems.append(f"v0: {name}: no score-function gradient was logged "
                                f"(sf_present = 0) yet frac_sf_zero is "
                                f"{v0arm.get('frac_sf_zero')!r}, not null: a fraction over an "
                                f"empty denominator is not a measurement")
        if v0arm.get("frac_task_grad_degenerate") != d["frac_task_grad_degenerate"]:
            problems.append(
                f"v0: {name}: {V0REL} stores frac_task_grad_degenerate = "
                f"{v0arm.get('frac_task_grad_degenerate')!r} but "
                f"{agentarm.get('degenerate_step_count')} of {v0arm.get('records')} steps is "
                f"{d['frac_task_grad_degenerate']!r} ({AREL} x {V0REL})")
        elif d["frac_task_grad_degenerate"] is not None:
            print(f"  v0: {name}: frac_task_grad_degenerate {d['frac_task_grad_degenerate']} "
                  f"re-derived from {agentarm.get('degenerate_step_count')}/"
                  f"{v0arm.get('records')}")
        for key, derived_key in (("steps_with_nonzero_gradient",
                                  "steps_with_nonzero_gradient_from_v0"),):
            stored, want = v0arm.get(key), d[derived_key]
            if stored != want:
                problems.append(f"v0: {name}: {V0REL} stores {key} = {stored!r} but the "
                                f"counts give {want!r}")
            elif stored is not None:
                print(f"  v0: {name}: {key} {stored} re-derived from the counts")
        other = d["steps_with_nonzero_gradient_from_agent"]
        if sf_present and other is not None and other != v0arm.get("steps_with_nonzero_gradient"):
            problems.append(
                f"v0: {name}: steps_with_nonzero_gradient is "
                f"{v0arm.get('steps_with_nonzero_gradient')!r} in {V0REL} but "
                f"n_step_records - sf_zero_step_count is {other} in {AREL}: the two records "
                f"derive different effective-step counts from the same log")

        # ---- 3. the dual reading ------------------------------------------------
        # Key PRESENCE, not value: a11_6_literal.pass is legitimately null for the arm that
        # logs no score-function gradient, and `None` is a reading while an absent key is not.
        # Key PRESENCE, tested with `in` and never with truthiness: a11_6_literal.pass is
        # legitimately null for the arm that logs no score-function gradient, `pass` is
        # legitimately false, and a MISSING key is a different thing from either.
        for present, why in (
                ("pass" in v0arm,
                 "no `pass` key at all, so the Amendment-29 reading is not on record"),
                ("a11_6_literal" in v0arm,
                 "no `a11_6_literal` block, so the pre-registration as literally written is "
                 "not computed beside the redefinition that superseded it"),
                ("pass" in lit,
                 "`a11_6_literal` carries no `pass`, so the dual reading is only half present")):
            if not present:
                problems.append(f"v0: {name}: {V0REL} has {why}. A record that reports only "
                                f"the reading under which it passed has chosen its own verdict, "
                                f"which is the defect Amendment 30.10 exists to prevent")
        if "a11_6_literal" in v0arm and "pass" in lit:
            print(f"  v0: {name}: DUAL READING present -- Amendment 29 `pass` = "
                  f"{v0arm.get('pass')!r} beside A11.6-literal `a11_6_literal.pass` = "
                  f"{lit.get('pass')!r}")
        if v0arm.get("guard_branch_has_no_upper_bound") is not True:
            problems.append(f"v0: {name}: {V0REL} does not carry "
                            f"`guard_branch_has_no_upper_bound: true` "
                            f"(found {v0arm.get('guard_branch_has_no_upper_bound')!r}), so the "
                            f"unbounded guard branch Amendment 30.10 discloses is not on record")
        if lit.get("pass") is False and not (v0arm.get("warnings") or []):
            problems.append(f"v0: {name}: A11.6 as literally pre-registered FAILS "
                            f"({lit.get('frac_sf_zero')} of the score-function gradient is zero) "
                            f"while Amendment 29 passes, and {V0REL} carries an EMPTY warnings "
                            f"list: the discrepancy is then visible only to a reader who diffs "
                            f"two keys, which is not a disclosure")
        elif v0arm.get("warnings"):
            print(f"  v0: {name}: {len(v0arm['warnings'])} warning(s) recorded beside the pass")

    if v0.get("V0_pass") is not all(a["v0"].get("pass") for a in S["arms"].values()):
        problems.append(f"v0: the top-level V0_pass is {v0.get('V0_pass')!r} while the arms are "
                        f"{ {k: a['v0'].get('pass') for k, a in sorted(S['arms'].items())} }")

    # ---- 3. the prose -----------------------------------------------------------
    # The NUMERIC anchors run over the whole document.  Each pattern is written so it can only
    # match a statement of that one quantity, and this manuscript states V0's step count in TWO
    # places -- related work ("ran the full $200$ steps they were pre-registered to run") and
    # the agent section ("both ran the $200$ steps").  Scoping the anchor to one paragraph
    # would leave the sibling site ungated, which is the defect this repository keeps naming,
    # so one pattern covers both and every match must equal the record.
    # The STRUCTURAL dual-reading checks ARE scoped to the V0 paragraph, because phrases like
    # "no upper bound" and "Amendment 29" could legitimately appear about something else.
    src = Path(args.tex).read_text()
    region, n_anchor = v0_paragraph(src)
    if not region.strip():
        problems.append(f"v0: no paragraph of main.tex carries any of the anchors "
                        f"{[a[:40] for a in V0_PARAGRAPH_ANCHORS]}, so the V0 write-up is gone "
                        f"and every structural check below would be a dead check; either the "
                        f"result was cut or this gate's anchors drifted")
    elif "validity audit V0" not in region:
        problems.append("v0: the paragraph selected as V0's write-up does not mention the "
                        "validity audit V0, so the selection anchors have drifted onto a "
                        "different paragraph and the structural checks below would be reading "
                        "the wrong text")
    grpo = S["arms"].get("grpo")
    ste = S["arms"].get("ste")
    if grpo and ste:
        g, s_ = grpo["v0"], ste["v0"]
        gl = g.get("a11_6_literal") or {}
        gd = grpo["derived"]
        rlfs = s_.get("rescale_large_finite_steps") or []
        specs = [
            # both sites of the step count, one pattern
            (r"ran the (?:full |)\$(\d+)\$ steps they were\s+pre-registered to run",
             g.get("records"), "the number of steps both arms ran, at every site that states it"),
            (r"under A11\.6 as \\emph\{literally\}\s+pre-registered "
             r"\(\$\\texttt\{sf\} = 0\$ on \$(\d+)\\%\$ of logged epochs, "
             r"zero tolerance\)",
             "0", "the zero tolerance A11.6 pre-registered, quoted as the literal criterion"),
            (r"exactly zero on \$(\d+)\$ of \$(\d+)\$ steps, \$([\d.]+)\\%\$",
             (gl.get("n_sf_zero"), g.get("records"), gd["frac_sf_zero_pct_1dp"]),
             "the scalar-reward arm's score-function zero steps, their denominator and "
             "their percentage"),
            (r"improved on \$(\d+)\$ effective steps\s+of \$(\d+)\$",
             (g.get("steps_with_nonzero_gradient"), g.get("records")),
             "the effective steps the scalar-reward arm improved on"),
            (r"logs\s+\$(\d+)\$ degenerate-gradient steps of \$(\d+)\$",
             ((grpo["agent"] or {}).get("degenerate_step_count"), g.get("records")),
             "the scalar-reward arm's degenerate-gradient step count and its denominator"),
            (r"one step whose rescale falls in\s+\$\[10\^6, 10\^\{11\}\)\$---step "
             r"\$(\d+)\$, the\s+first",
             (rlfs[0].get("step") if rlfs else None),
             "the step whose rescale falls in [1e6, 1e11) in the coupled arm"),
            (r"at or above the \$10\^\{(\d+)\}\$ saturation signature", "11",
             "the exponent of the saturation signature A11.6 pre-registered at zero tolerance"),
        ]
        specs = [sp for sp in specs if sp[1] is not None and
                 (not isinstance(sp[1], tuple) or all(x is not None for x in sp[1]))]
        claim_scoped(src, specs, problems, "v0")

        for name, a in sorted(S["arms"].items()):
            population_guard(a["v0"], a["agent"] or {}, src, name, problems)
            ag = a["agent"] or {}
            if ag.get("rescale_ge_1e11_steps"):
                problems.append(f"v0: {name}: {AREL} lists steps "
                                f"{ag['rescale_ge_1e11_steps']} at or above the 1e11 saturation "
                                f"signature, so main.tex's 'no step anywhere in the repository "
                                f"at or above the $10^{{11}}$ saturation signature' is false")
            if a["v0"].get("frac_rescale_saturated"):
                problems.append(f"v0: {name}: {V0REL} records frac_rescale_saturated = "
                                f"{a['v0']['frac_rescale_saturated']}, so the same sentence is "
                                f"false from the other record too")
        print("  v0: no step reaches the 1e11 saturation signature in either record, as the "
              "prose states")
        # the band the prose names must be the band the recorded rescale actually falls in
        for entry in rlfs:
            val = entry.get("task_grad_rescale")
            if not (val is not None and 1e6 <= val < 1e11):
                problems.append(f"v0: ste: {V0REL} lists a step-1 rescale of {val!r}, which is "
                                f"not inside the $[10^6, 10^{{11}})$ band main.tex names for it")
        if s_.get("n_rescale_large_finite") != len(rlfs) or s_.get("n_rescale_large_finite") != 1:
            problems.append(f"v0: ste: {V0REL} records n_rescale_large_finite = "
                            f"{s_.get('n_rescale_large_finite')!r} with {len(rlfs)} step(s) "
                            f"listed, but main.tex says 'one step whose rescale falls in "
                            f"[1e6, 1e11)'")
        if g.get("n_rescale_large_finite"):
            problems.append(f"v0: grpo: {V0REL} records n_rescale_large_finite = "
                            f"{g.get('n_rescale_large_finite')!r}, but main.tex says the "
                            f"scalar-reward arm 'logs none'")

        # ---- the dual reading, in the PROSE and not only in the record -----------
        if region.strip() and "validity audit V0" in region:
            both = [
                (r"passes under Amendment 29's mechanistic redefinition",
                 "the reading under which V0 passed"),
                (r"under A11\.6 as \\emph\{literally\}\s+pre-registered",
                 "the reading under which the scalar-reward arm failed"),
                (r"the scalar-reward arm\s+\\emph\{fails\}",
                 "the literal failure itself, stated as a failure"),
                (r"the audit's guard branch still has no upper bound",
                 "the unbounded guard branch Amendment 30.10 discloses"),
                (r"Both readings are computed side by side in "
                 r"\\texttt\{records/e12\\_v0\\_audit\.json\}",
                 "the record both readings are shipped in, named where they are quoted"),
                (r"pass clause 3 while failing clause 5",
                 "what an unbounded guard branch would let through"),
                (r"The coupled arm logs no\s+score-function gradient and is not "
                 r"subject to that clause",
                 "the coupled arm's exemption from the clause"),
                (r"no step anywhere in\s+the repository at or above the \$10\^\{11\}\$ "
                 r"saturation signature",
                 "the absence of the one signature pre-registered at zero tolerance"),
                (r"the coupled arm\s+logs none",
                 "the coupled arm's single large-finite rescale, contrasted with the scalar "
                 "arm's none"),
            ]
            for pat, what in both:
                if not re.search(pat, region, re.S):
                    problems.append(f"v0: main.tex's V0 paragraph no longer states {what} "
                                    f"(pattern {pat!r}). {V0REL} carries both readings; a body "
                                    f"that quotes only the passing one has chosen its own "
                                    f"verdict, which is the defect Amendment 30.10 exists to "
                                    f"prevent")
                else:
                    print(f"  v0: the V0 paragraph carries {what}")
            sl = s_.get("a11_6_literal") or {}
            if s_.get("sf_present") != 0 or sl.get("pass") is not None:
                problems.append(f"v0: ste: main.tex says the coupled arm logs no score-function "
                                f"gradient and is not subject to the clause, but {V0REL} gives "
                                f"it sf_present = {s_.get('sf_present')!r} and "
                                f"a11_6_literal.pass = {sl.get('pass')!r}: the exemption in the "
                                f"prose is not the exemption in the record, and an undefined "
                                f"criterion quoted as a met one is the same defect")
            else:
                print("  v0: the coupled arm's exemption agrees -- sf_present = 0 and "
                      "a11_6_literal.pass = null in the record")
            # ---- the SIBLING site: related work also states V0's verdict ----------
            sib = re.search(r"passed the\s+validity audit V0 with no reasons recorded "
                            r"against either", src, re.S)
            if not sib:
                problems.append("v0: the related-work sentence that also states V0's verdict "
                                "('passed the validity audit V0 with no reasons recorded "
                                "against either') is gone from main.tex. It is the sibling of "
                                "the agent-section statement and this gate pinned only the one "
                                "site before, so its disappearance is reported rather than "
                                "passing unnoticed")
            else:
                for name, a in sorted(S["arms"].items()):
                    if a["v0"].get("reasons") or a["v0"].get("pass") is not True:
                        problems.append(
                            f"v0: {name}: related work says V0 passed 'with no reasons recorded "
                            f"against either' arm, but {V0REL} gives this arm pass = "
                            f"{a['v0'].get('pass')!r} with reasons "
                            f"{[str(x)[:80] for x in (a['v0'].get('reasons') or [])][:2]}")
                print("  v0: the related-work sibling site agrees -- both arms pass with "
                      "reasons == [] in the record")

    if problems:
        print("V0 GATE FAILED:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1
    print(f"v0 numbers agree with {V0REL} and {AREL} (both records reconciled, both readings "
          f"present in the record and in the prose)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
