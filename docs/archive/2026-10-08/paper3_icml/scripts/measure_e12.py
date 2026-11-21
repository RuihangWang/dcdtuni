#!/usr/bin/env python3
"""Gate every E12 (post-training arms) claim the manuscript makes against its record.

Reads `records/e12_agent_result.json`, written by `extract_agent_result.py` from the eval
blocks `train_rl.py` emits.  Two jobs:

  1. STRUCTURAL, and the reason this gate exists.  Amendment 30.1 fixes in advance what the
     paper may claim per branch of the outcome.  This enforces those clauses mechanically:
     the direction of the result, the feasibility disclosure that must accompany any regret,
     the ban on quoting a `regret_mean` from a split containing an infeasible task, and the
     requirement that an unmatched physics budget be stated wherever the arms are compared.
     A prose sentence that the record does not support fails here even if every digit in it
     is correct.

  2. NUMERIC, claim-scoped: every anchored number must equal the record, and zero matches is
     a failure, because an anchored pattern that stops matching is a gate that has gone dead.

`--print-values` emits the exact strings the patterns expect, so the prose is written FROM
the record rather than next to it.  That is the only reliable way to keep the two in step:
this campaign's standing observation is that a prose-carried number has been wrong every
single time it was checked.

Usage: measure_e12.py [--check] [--print-values] [--record PATH] [--tex PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SPLITS = ("val", "test", "ood")


def claim_scoped(src, specs, problems, where):
    for pattern, expected, label in specs:
        hits = re.findall(pattern, src, re.S)
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



AGENT_SENTENCE = re.compile(
    r"adjoint-coupled arm|scalar-reward arm|feasible rate|post-training|"
    r"physics budget|forward solves per step", re.I)
DECIMAL_3PLUS = re.compile(r"[-+]?\d+\.\d{3,}")


def record_renderings(rec):
    """Every 3-, 4- and 5-decimal rendering, signed and unsigned, of every
    regret_median and feasible_rate in the record.  A value may only appear in the
    abstract in one of these forms; anything else is a number this record does not
    hold, whatever sentence it is sitting in."""
    allowed = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("regret_median", "feasible_rate") and isinstance(v, (int, float)) \
                        and not isinstance(v, bool):
                    for nd in (3, 4, 5):
                        allowed.add(f"{v:.{nd}f}")
                        allowed.add(f"{v:+.{nd}f}")
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(rec.get("arms"))
    return allowed


def abstract_sweep(rec, src, problems):
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", src, re.S)
    if not m:
        problems.append("e12: main.tex has no abstract environment, so the abstract's agent "
                        "numbers are unchecked and a check with nothing to check is not a pass")
        return
    flat = " ".join(m.group(1).split())
    sents = [x for x in re.split(r"(?<=\.)\s+", flat) if AGENT_SENTENCE.search(x)]
    if not sents:
        problems.append("e12: the abstract has no sentence about the agent arms, so either the "
                        "result was cut or the sweep's sentence filter has drifted; both are "
                        "failures rather than a pass")
        return
    allowed = record_renderings(rec)
    if not allowed:
        problems.append("e12: no regret_median or feasible_rate could be read out of the "
                        "record, so the abstract sweep has no allowed set")
        return
    n = 0
    for sent in sents:
        for tok in DECIMAL_3PLUS.findall(sent):
            n += 1
            if tok not in allowed:
                nearest = sorted(allowed, key=lambda a: abs(float(a) - float(tok)))[:3]
                problems.append(
                    f"e12: the abstract states {tok}, which is not a rendering of any "
                    f"regret_median or feasible_rate in records/e12_agent_result.json "
                    f"(nearest held values: {nearest}) -- in: {sent[:150]!r}")
    if n and not any(p.startswith("e12: the abstract states") for p in problems):
        print(f"  e12: abstract sweep -- all {n} decimal(s) of 3+ places in the "
              f"{len(sents)} agent sentence(s) are renderings of record values")


def last_block(arm):
    return arm["blocks"][-1]


def block_at(arm, step):
    for b in arm["blocks"]:
        if b["step"] == step:
            return b
    return None


def sgn(v, nd=4):
    return f"{v:+.{nd}f}"


def summarise(rec):
    """The values the prose must carry, as LaTeX-ready strings."""
    a = rec["arms"]
    ste, grpo = a["ste_s0"], a["grpo_s0"]
    sb, gb = last_block(ste), last_block(grpo)
    init = block_at(ste, 0)
    out = {
        "steps_requested": ste.get("steps_requested"),
        "ste_last_step": ste["last_step_logged"],
        "grpo_last_step": grpo["last_step_logged"],
        "is_final": rec["is_final"],
        "init": {s: sgn(init[s]["regret_median"]) for s in SPLITS},
        "init_feas": {s: f"{init[s]['feasible_rate']:.3f}" for s in SPLITS},
        "ste": {s: sgn(sb[s]["regret_median"]) for s in SPLITS},
        "ste_feas": {s: f"{sb[s]['feasible_rate']:.3f}" for s in SPLITS},
        "grpo": {s: sgn(gb[s]["regret_median"]) for s in SPLITS},
        "grpo_feas": {s: f"{gb[s]['feasible_rate']:.3f}" for s in SPLITS},
        "ste_fwd_per_step": f"{rec['arms_budget']['forward_solves_per_step']['ste_s0'][0]:.0f}",
        "grpo_fwd_per_step": f"{rec['arms_budget']['forward_solves_per_step']['grpo_s0'][0]:.0f}",
        "physics_budget_matched": rec["arms_budget"]["physics_budget_matched"],
        "ste_adjoint_per_block": sb["adjoint_solves_cumulative"],
        "grpo_adjoint_total": sum((b["adjoint_solves_cumulative"] or 0) for b in grpo["blocks"]),
        "oom": (ste["oom_total"], grpo["oom_total"]),
        "grpo_degen": (grpo["degenerate_step_count"], grpo["n_step_records"]),
        "ste_jscale": (f"{ste['j_scale_min']:.0f}", f"{ste['j_scale_max']:.0f}"),
        "ste_jmean": (f"{ste['train_J_mean_min']:.0f}", f"{ste['train_J_mean_max']:.3g}"),
        "ste_rescale_band_steps": ste["rescale_1e6_to_1e11_steps"],
        "ste_saturated_steps": ste["rescale_ge_1e11_steps"],
        "ste_worse_than_init_on_all": all(
            sb[s]["regret_median"] > init[s]["regret_median"] for s in SPLITS),
        "grpo_better_than_init_on_val": gb["val"]["regret_median"] < init["val"]["regret_median"],
        "ste_worse_than_grpo_on_val": sb["val"]["regret_median"] > gb["val"]["regret_median"],
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--print-values", action="store_true")
    ap.add_argument("--record", default=str(HERE / "records" / "e12_agent_result.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    args = ap.parse_args()

    rp = Path(args.record)
    if not rp.exists():
        print(f"FAIL: no E12 record at {rp}; the agent numbers in the body would be "
              "unchecked. Regenerate with scripts/extract_agent_result.py --force once the "
              "arms have finished.", file=sys.stderr)
        return 2
    rec = json.loads(rp.read_text())
    v = summarise(rec)
    if args.print_values:
        print(json.dumps(v, indent=1))
        return 0

    src = Path(args.tex).read_text()
    problems = []

    # ---- record integrity -----------------------------------------------------
    for arm in ("ste_s0", "grpo_s0"):
        a = rec["arms"][arm]
        if a["source_mismatches"]:
            problems.append(f"e12: {arm}'s log.jsonl and stdout disagree in "
                            f"{len(a['source_mismatches'])} place(s): {a['source_mismatches'][:2]}")
        if a["oom_total"]:
            problems.append(f"e12: {arm} recorded {a['oom_total']} OOM event(s), so the run is "
                            "not the pre-registered one")
        if a["rescale_ge_1e11_steps"]:
            problems.append(f"e12: {arm} saturated the task-gradient rescale at steps "
                            f"{a['rescale_ge_1e11_steps']}, which is the A11.6 signature")
        if not a["done_record_present"]:
            problems.append(f"e12: {arm} has no `done` record, so this is a snapshot of a run "
                            f"still in flight at step {a['last_step_logged']} and may not be "
                            "presented as a completed result")
    init_s = block_at(rec["arms"]["ste_s0"], 0)
    init_g = block_at(rec["arms"]["grpo_s0"], 0)
    for s in SPLITS:
        if abs(init_s[s]["regret_median"] - init_g[s]["regret_median"]) > 1e-12:
            problems.append(f"e12: the two arms' step-0 {s} blocks differ, so they do not start "
                            "from the same adapter and are not comparable")

    # ---- A30.1e: the direction decides what may be claimed ---------------------
    adjoint_helps = (not v["ste_worse_than_grpo_on_val"]) and v["grpo_better_than_init_on_val"] is False
    if v["ste_worse_than_grpo_on_val"] or v["ste_worse_than_init_on_all"]:
        banned = [
            (r"coupling the adjoint[^.]{0,120}improve", "the adjoint coupling improves the agent"),
            (r"the pathwise arm[^.]{0,120}(better|beats|outperform)", "the pathwise arm wins"),
            (r"physics gradient[^.]{0,120}improve", "the physics gradient improves the agent"),
        ]
        for pat, what in banned:
            m = re.search(pat, src, re.S | re.I)
            if m:
                problems.append(f"e12: the record says the adjoint-coupled arm is worse, but "
                                f"main.tex still asserts {what}: ...{m.group(0)[:90]}...")
        if "worse than its own starting point" not in src:
            problems.append("e12: the body no longer says the adjoint-coupled arm is worse than "
                            "its own starting point, which is the finding")

    # ---- A30.1d: regret_mean is not quotable where a task is infeasible --------
    inf_splits = {s for s in SPLITS
                  if last_block(rec["arms"]["ste_s0"])[s]["n_infeasible"]
                  or last_block(rec["arms"]["grpo_s0"])[s]["n_infeasible"]}
    # Scoped to the two places this experiment is written up.  The phrase may legitimately
    # appear elsewhere about another experiment -- and "negative regret means the agent's
    # design beats that reference" is the VERB, not the statistic, which an unscoped scan
    # cannot tell apart.  A gate that fires on text it is not about gets disabled, not obeyed.
    def _region(label):
        i = src.find(label)
        if i < 0:
            return ""
        j = src.find("\\section", i + len(label))
        return src[i:] if j < 0 else src[i:j]

    agent_txt = _region("\\label{sec:agent}") + _region("\\label{app:agent}")
    if not agent_txt.strip():
        problems.append("e12: neither sec:agent nor app:agent is in main.tex, so the agent "
                        "result is not written up and every E12 anchor below is a dead check")
    if inf_splits and re.search(r"regret[ _-]?mean(?!s\b)", agent_txt, re.I):
        problems.append(f"e12: the agent sections quote a regret mean, but {sorted(inf_splits)} "
                        "contain an infeasible task whose 1e9 penalty makes the mean "
                        "uninterpretable (A30.1d)")

    # ---- A30.1c: a regret must travel with its feasible rate -------------------
    # Every sentence carrying a val/test/ood regret median must also carry a feasible rate.
    for m in re.finditer(r"[^.]*?regret[^.]*?\.", src, re.S):
        sent = m.group(0)
        if re.search(r"\\times10|feasible", sent):
            continue
        if re.search(r"[-+]0\.\d{4,}", sent):
            problems.append("e12: a sentence quotes a regret without a feasible rate beside it "
                            f"(A30.1c): {sent.strip()[:110]}...")

    # ---- A30.1c: a gain bought with lost feasibility may not be quoted alone ----
    for arm, label in (("ste_s0", "coupled"), ("grpo_s0", "scalar-reward")):
        a = rec["arms"][arm]
        ini, fin = block_at(a, 0), a["blocks"][-1]
        for s in SPLITS:
            if fin[s]["feasible_rate"] < ini[s]["feasible_rate"]:
                dropped = f"{ini[s]['feasible_rate']:.3f}"
                if dropped not in src or f"{fin[s]['feasible_rate']:.3f}" not in src:
                    problems.append(
                        f"e12: the {label} arm's {s} feasible rate falls from "
                        f"{ini[s]['feasible_rate']:.3f} to {fin[s]['feasible_rate']:.3f} and "
                        "main.tex does not state both, so a gain on that arm reads as "
                        "unqualified (A30.1c)")

    # ---- the unmatched physics budget must be disclosed ------------------------
    if not v["physics_budget_matched"]:
        if not (v["ste_fwd_per_step"] in src and v["grpo_fwd_per_step"] in src):
            problems.append(f"e12: the arms are not physics-budget matched "
                            f"({v['ste_fwd_per_step']} vs {v['grpo_fwd_per_step']} forward solves "
                            "per step) and main.tex does not state both rates, so the comparison "
                            "reads as matched when it is not")

    lo, hi = (re.escape(x) for x in v["ste_jscale"])
    jm0, jm1 = (re.escape(x) for x in v["ste_jmean"])
    fs, fg = v["ste_fwd_per_step"], v["grpo_fwd_per_step"]
    # Values interpolated into a pattern must be re.escape'd: a training objective recorded as
    # 1.89e+05 contains a `+` that would otherwise mean "one or more of the preceding", and the
    # pattern would silently never match -- which this gate reports as a dead anchor, but only
    # after the reader has been told the number is checked.
    lo, hi = v["ste_jscale"]
    jm0, jm1 = v["ste_jmean"]
    fs, fg = v["ste_fwd_per_step"], v["grpo_fwd_per_step"]
    sf = [v["ste_feas"][k] for k in SPLITS]
    gf = [v["grpo_feas"][k] for k in SPLITS]
    # Each pattern is anchored to the sentence that carries the number, so a digit cannot be
    # validated against an unrelated occurrence of the same string elsewhere in the document,
    # and ZERO matches is a failure: an anchor that stops matching is a gate that has gone dead.
    specs = [
        (r"over two \$(\d+)\$-step arms", v["steps_requested"],
         "the number of steps each arm was pre-registered to run"),
        (r"\\emph\{val\}\s*\$([-+\d.]+)\$\s*against \$([-+\d.]+)\$,\s*\\emph\{test\}\s*"
         r"\$([-+\d.]+)\$ against\s*\$([-+\d.]+)\$,\s*\\emph\{ood\}\s*\$([-+\d.]+)\$"
         r"\s*against \$([-+\d.]+)\$",
         (v["ste"]["val"], v["init"]["val"], v["ste"]["test"], v["init"]["test"],
          v["ste"]["ood"], v["init"]["ood"]),
         "the coupled arm's three final regrets against its own initialisation"),
        # A30.1c is enforced by the ANCHOR, not by a separate scan: this pattern cannot match
        # unless each regret appears in the same sentence as its own feasible rate, so quoting
        # the gain without the fall stops being a prose problem and becomes a gate failure.
        (r"to \$([-+\d.]+)\$ on \\emph\{val\}, where it\s*stays feasible on all \$(\d+)\$ "
         r"tasks, and to \$([-+\d.]+)\$ on \\emph\{test\}, whose feasible rate\s*falls "
         r"from\s*\$([\d.]+)\$ to\s*\$([\d.]+)\$",
         (v["grpo"]["val"], "8", v["grpo"]["test"], v["init_feas"]["test"], v["grpo_feas"]["test"]),
         "the scalar-reward arm's val and test regrets WITH their feasible rates in the same "
         "sentence (A30.1c)"),
        (r"\$(" + fs + r")\$ forward solves per step against \$(" + fg + r")\$", (fs, fg),
         "the two arms' physics budgets per step"),
        # One group capturing the whole bracketed pair: interpolating a recorded value such as
        # 1.89e+05 into a pattern makes its `+` a quantifier, and the anchor then never matches.
        (r"recorded scale spans\s*\$\[([^\]]+)\]\$", f"{lo}, {hi}",
         "the pathwise arm's EMA normaliser range"),
        (r"training objective spans\s*\$\[([^\]]+)\]\$", f"{jm0}, {jm1}",
         "the pathwise arm's training objective range"),
        (r"logs\s*\$(\d+)\$ degenerate-gradient steps of \$(\d+)\$",
         (str(v["grpo_degen"][0]), str(v["grpo_degen"][1])),
         "the scalar-reward arm's degenerate-gradient step count"),
        (r"feasible rates are ste \$([\d.]+)/([\d.]+)/([\d.]+)\$ and\s*grpo\s*"
         r"\$([\d.]+)/([\d.]+)/([\d.]+)\$", tuple(sf + gf),
         "both arms' feasible rates at the final step"),
        (r"\\emph\{ood\} feasible rate falls from \$([\d.]+)\$ at initialisation to\s*"
         r"\$([\d.]+)\$", (v["init_feas"]["ood"], gf[2]),
         "the scalar-reward arm's out-of-distribution feasibility collapse"),
    ]
    specs = [s for s in specs if s[1] is not None]
    claim_scoped(src, specs, problems, "e12")

    abstract_sweep(rec, src, problems)

    if problems:
        print("E12 GATE FAILED:", file=sys.stderr)
        for pr in problems:
            print("  -", pr, file=sys.stderr)
        return 1
    print("e12 claims agree with the record (direction, feasibility disclosure and "
          "budget mismatch included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
