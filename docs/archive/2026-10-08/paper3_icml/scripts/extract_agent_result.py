"""Derive the E12 RL-arm comparison numbers from the run logs.

Reads the per-eval-block records that train_rl.py writes (records carrying an
"eval" key) from BOTH log.jsonl and the stdout log, cross-checks that the two
agree, and emits one JSON record.  Every number quoted about the agent arms in
the manuscript must come from this record, never from prose.
"""
import json, os, sys, hashlib

BASE = os.path.expanduser(
    "~/mycode/dcdtuni/ddl/case/benchC/E12_geom_posttrain/loggers/20260917T1355Z_gpuserver")
ARMS_DEFAULT = ["ste_s0", "grpo_s0"]
# E12d joins the table when it finishes, not before: a partial arm in a committed
# snapshot is a snapshot of a run in flight, which the refusal below exists to stop.
# Only --arms=X is accepted.  The `--arms X` spelling was tried first and silently
# made X the output path, which is the defect the _args filter below exists to
# prevent; a flag whose value can be mistaken for a positional is a flag that
# should not take a separate value.
ARMS = list(ARMS_DEFAULT)
for _a in sys.argv[1:]:
    if _a.startswith("--arms="):
        _given = [x for x in _a.split("=", 1)[1].split(",") if x]
        if not _given:
            raise SystemExit("--arms= took an empty list; refusing to extract nothing")
        ARMS = _given
    elif _a == "--arms":
        raise SystemExit("--arms takes --arms=arm1,arm2 (no separate value): a separate "
                         "value does not start with a dash and would be read as the "
                         "output path, writing the record to a file named after the arms")
SPLITS = ["val", "test", "ood"]


def jsonl_records(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def stdout_records(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def agg_of(rec, split):
    return rec["eval"][split]["agg"]



def _last_non_null(xs):
    got = [x for x in xs if x is not None]
    return int(got[-1]) if got else None


def _arg_flag(arm, flag):
    """Read a boolean flag out of the arm's own args.json, or None if there is none.

    Read from the artifact rather than assumed: an arm whose counter is zero because
    the flag was never set is a different experiment from one whose counter is zero
    because the events never happened, and only the second is a null control.
    """
    f = os.path.join(BASE, arm, "args.json")
    if not os.path.exists(f):
        return None
    try:
        d = json.load(open(f))
    except Exception:
        return None
    if flag in d:
        return bool(d[flag])
    # argparse-style stores keep the value under the dest name
    return bool(d.get(flag.replace("-", "_"), None)) if any(
        k.replace("-", "_") == flag.replace("-", "_") for k in d) else None


def _jmean_where(step_view, key):
    """[min, max] of J_mean over the steps whose counter for `key` is zero.

    This is the pair that makes a zero counter informative rather than merely
    absent: if the normaliser ranged over four orders of magnitude on steps where
    not one sample was masked, then no masked-scale event was available to explain
    anything, which is the arithmetic section 6 states.
    """
    vals = [r.get("J_mean") for r in step_view
            if r.get(key) is not None and int(r[key]) == 0 and r.get("J_mean") is not None]
    if not vals:
        return None
    return [min(vals), max(vals)]

def summarize(arm):
    jl = [r for r in jsonl_records(os.path.join(BASE, arm, "log.jsonl")) if "eval" in r]
    so = [r for r in stdout_records(os.path.join(BASE, arm + ".stdout.log")) if "eval" in r]
    # cross-check: the two sources must agree on the eval blocks they share
    mismatches = []
    so_by_step = {r["step"]: r for r in so}
    for r in jl:
        o = so_by_step.get(r["step"])
        if o is None:
            mismatches.append({"step": r["step"], "issue": "absent from stdout"})
            continue
        for s in SPLITS:
            a, b = agg_of(r, s), agg_of(o, s)
            for k in ("regret_median", "feasible_rate", "J_mean"):
                if abs(a[k] - b[k]) > 1e-12 * max(1.0, abs(a[k])):
                    mismatches.append({"step": r["step"], "split": s, "field": k,
                                       "jsonl": a[k], "stdout": b[k]})
    steps = [r for r in jl]
    steps.sort(key=lambda r: r["step"])
    blocks = []
    for r in steps:
        blk = {"step": r["step"]}
        for s in SPLITS:
            a = agg_of(r, s)
            n_inf = sum(1 for t in r["eval"][s]["per_task"] if not t["feasible"])
            blk[s] = {
                "regret_median": a["regret_median"],
                "regret_median_feasible_only": a["regret_median_feasible_only"],
                "regret_mean": a["regret_mean"],
                "feasible_rate": a["feasible_rate"],
                "n_infeasible": n_inf,
                "J_mean": a["J_mean"],
                "J_excess_median": a["J_excess_median"],
            }
        blk["train_J_mean"] = r.get("J_mean")
        blk["train_J_best"] = r.get("J_best")
        blk["j_scale"] = r.get("j_scale")
        blk["task_grad_degenerate"] = r.get("task_grad_degenerate")
        blk["adjoint_solves_cumulative"] = (r.get("physics") or {}).get("adjoint")
        blk["forward_solves_cumulative"] = (r.get("physics") or {}).get("forward")
        blk["adjoint_solves_per_step"] = (
            None if not r.get("step") else
            round(((r.get("physics") or {}).get("adjoint") or 0) / r["step"], 3))
        blk["forward_solves_per_step"] = (
            None if not r.get("step") else
            round(((r.get("physics") or {}).get("forward") or 0) / r["step"], 3))
        blk["grad_norms"] = r.get("grad_norms")
        blk["probe_arith_acc"] = (r.get("probe") or {}).get("arith_acc")
        blocks.append(blk)

    allrecs = jsonl_records(os.path.join(BASE, arm, "log.jsonl"))
    step_recs = [r for r in allrecs if "step" in r and "eval" not in r]
    step_recs.sort(key=lambda r: r["step"])

    # The step-counted population, which is the one the run was configured for and
    # the one records/e12_v0_audit.json uses: distinct training steps 1..max.  The
    # record-filtered population above is kept and named rather than replaced,
    # because the two differ by a real amount (eval payloads ride on some training
    # steps) and a record that silently changed its denominator is how the
    # manuscript came to print 63/197 two sentences after citing 15/200.
    step_bearing = [r for r in allrecs if r.get("step") is not None]
    training_steps = sorted({int(r["step"]) for r in step_bearing if int(r["step"]) >= 1})
    by_step = {}
    for r in step_bearing:
        st = int(r["step"])
        if st < 1:
            continue
        # prefer the record that actually carries the training payload
        if st not in by_step or r.get("task_grad_rescale") is not None:
            by_step[st] = r
    step_view = [by_step[st] for st in training_steps]
    degen_steps_all = [st for st in training_steps
                       if by_step[st].get("task_grad_degenerate")]
    sfzero_steps_all = [st for st in training_steps
                        if (by_step[st].get("grad_norms") or {}).get("sf") == 0]

    tgr = [(r["step"], r.get("task_grad_rescale")) for r in step_recs
           if r.get("task_grad_rescale") is not None]
    degen_steps = degen_steps_all
    jm = [(r["step"], r.get("J_mean")) for r in step_recs if r.get("J_mean") is not None]
    js = [(r["step"], r.get("j_scale")) for r in step_recs if r.get("j_scale") is not None]
    return {
        "arm": arm,
        "n_eval_blocks": len(blocks),
        "blocks": blocks,
        "last_step_logged": (step_recs[-1]["step"] if step_recs else None),
        "n_step_records": len(step_view),
        "n_step_records_non_eval_payloads": len(step_recs),
        "n_jsonl_lines": len(allrecs),
        "n_step_bearing_lines": len(step_bearing),
        "population": ("distinct training steps 1..max, the population the run was "
                       "configured for and the one records/e12_v0_audit.json uses; "
                       "n_step_records_non_eval_payloads is the narrower record-level "
                       "filter, kept and named because the two differ"),
        "sf_zero_step_count": len(sfzero_steps_all),
        "sf_zero_steps": sfzero_steps_all,
        # train_rl.py emits a pre-step and a post-step record that both carry
        # step == 1, so a record count is not a step count.  Counting distinct steps
        # removes the hazard; the duplicate is still named so the change is visible.
        "duplicate_step_values": sorted({st for st in
                                         (r["step"] for r in step_recs)
                                         if [q["step"] for q in step_recs].count(st) > 1}),
        "source_mismatches": mismatches,
        "rescale_ge_1e11_steps": [s for s, v in tgr if v >= 1e11],
        "rescale_1e6_to_1e11_steps": [s for s, v in tgr if 1e6 <= v < 1e11],
        "degenerate_step_count": len(degen_steps),
        "degenerate_steps": degen_steps,
        "train_J_mean_min": min((v for _, v in jm), default=None),
        "train_J_mean_max": max((v for _, v in jm), default=None),
        "j_scale_min": min((v for _, v in js), default=None),
        "j_scale_max": max((v for _, v in js), default=None),
        "oom_total": sum(int(r.get("oom") or 0) for r in step_recs),
        # The counter Amendment 30.2 pre-registers as the thing that decides which
        # branch the paper is in.  train_rl.py logs it cumulatively, so the last
        # non-null value is the total; `n_steps_logging_it` says how many step
        # records carry the field at all, because an arm that never logged it is
        # not an arm whose counter is zero and the two must not print the same.
        "penalty_masked_samples_total": _last_non_null(
            [r.get("penalty_masked_samples") for r in step_view]),
        "penalty_masked_groups_total": _last_non_null(
            [r.get("penalty_masked_groups") for r in step_view]),
        "penalty_mask_max_seen": max(
            [int(r["penalty_masked_samples"]) for r in step_view
             if r.get("penalty_masked_samples") is not None] or [0]),
        "n_steps_logging_penalty_mask": sum(
            1 for r in step_view if r.get("penalty_masked_samples") is not None),
        "pathwise_penalty_mask_flag": _arg_flag(arm, "pathwise_penalty_mask"),
        "train_J_mean_over_steps_with_zero_mask": _jmean_where(
            step_view, "penalty_masked_samples"),
        "done_record_present": any(r.get("event") == "done" for r in allrecs),
        "done_record": next((r for r in allrecs if r.get("event") == "done"), None),
        "args_sha": hashlib.sha256(open(os.path.join(BASE, arm, "args.json"), "rb").read()).hexdigest()[:16],
        # The pre-registered length of the run, read from the arm's own args rather than from
        # the log, so a run that stopped early cannot redefine what it was supposed to be.
        "steps_requested": json.load(open(os.path.join(BASE, arm, "args.json"))).get("steps"),
    }


def physics_rate(arm_blocks):
    rates = [b["forward_solves_per_step"] for b in arm_blocks if b["forward_solves_per_step"]]
    return sorted(set(rates))


_arms_tmp = {a: summarize(a) for a in ARMS}
_fwd = {a: physics_rate(_arms_tmp[a]["blocks"]) for a in ARMS}
_matched = len({tuple(v) for v in _fwd.values()}) <= 1 and all(_fwd.values())

out = {"arms_budget": {
    "forward_solves_per_step": _fwd,
    "physics_budget_matched": _matched,
    "note": ("the two arms do NOT spend the same number of physics evaluations per step, so a "
             "difference between them is not attributable to the gradient alone. The pathwise "
             "arm runs an extra forward pass per sample to build Jpath. The mismatch runs "
             "AGAINST the finding rather than for it: the arm with the adjoint also has the "
             "larger physics budget and still does worse, so the adverse result is not an "
             "artefact of the adjoint arm being starved. Any prose comparing the arms must "
             "state the two rates.")
    if not _matched else
    "the two arms spend the same number of physics evaluations per step",
}, "provenance": {
    "source": "train_rl.py per-eval-block records under "
              "ddl/case/benchC/E12_geom_posttrain/loggers/20260917T1355Z_gpuserver",
    "extractor": "extract_agent_result.py (this record is script-derived; no number here is prose-carried)",
    "note": "regret = (J_agent - J_oracle)/J_oracle; negative regret means the agent's hard design "
            "beats the reference labelled J_oracle. J=1e9 is the infeasibility penalty, so regret_mean "
            "on any split containing an infeasible task is not interpretable; medians and "
            "feasible-only medians are.",
}, "arms": _arms_tmp}

# Flags must not be mistaken for the output path: `--force` was silently treated as a
# filename, so the tracked record was left stale while a file named "--force" appeared in the
# paper directory and the gate then read last commit's numbers.
_args = [a for a in sys.argv[1:] if not a.startswith("-")]
dst = _args[0] if _args else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "records", "e12_agent_result.json")
out["snapshot_of_steps"] = {a: out["arms"][a]["last_step_logged"] for a in ARMS}
out["is_final"] = all(out["arms"][a]["done_record_present"] for a in ARMS)
if os.path.exists(dst) and "--force" not in sys.argv:
    prev = json.load(open(dst))
    if prev.get("snapshot_of_steps") != out["snapshot_of_steps"]:
        sys.stderr.write(
            "REFUSING to overwrite %s: it is a committed snapshot at steps %s and this run is "
            "at steps %s. Re-running this script mid-flight would silently replace committed "
            "evidence with a different snapshot of the same run. Pass --force once the arms "
            "have finished, or write to a scratch path.\n"
            % (dst, prev.get("snapshot_of_steps"), out["snapshot_of_steps"]))
        raise SystemExit(3)
with open(dst, "w") as fh:
    json.dump(out, fh, indent=2)
print("wrote", dst, "steps", out["snapshot_of_steps"], "final:", out["is_final"])

# human-readable table
print("\n%-9s %5s | %-34s | %-34s | %-34s" % ("arm", "step", "val", "test", "ood"))
print("%-9s %5s | %-34s | %-34s | %-34s" % ("", "", "rmed / feas / Jexc", "rmed / feas / Jexc", "rmed / feas / Jexc"))
for a in ARMS:
    d = out["arms"][a]
    for b in d["blocks"]:
        cells = []
        for s in SPLITS:
            cells.append("%+.5f / %.3f / %+.4f" % (b[s]["regret_median"], b[s]["feasible_rate"], b[s]["J_excess_median"]))
        print("%-9s %5s | %-34s | %-34s | %-34s" % (a, b["step"], cells[0], cells[1], cells[2]))
    print("   -> mismatches=%d  degen_steps=%d/%d  J_mean[%.4g,%.4g]  j_scale[%s,%s]  oom=%d" % (
        len(d["source_mismatches"]), d["degenerate_step_count"], d["n_step_records"],
        d["train_J_mean_min"] or -1, d["train_J_mean_max"] or -1,
        ("%.4g" % d["j_scale_min"]) if d["j_scale_min"] else "none",
        ("%.4g" % d["j_scale_max"]) if d["j_scale_max"] else "none",
        d["oom_total"]))
    print("   -> rescale>=1e11 steps: %s ; 1e6..1e11 steps: %s" % (
        d["rescale_ge_1e11_steps"], d["rescale_1e6_to_1e11_steps"]))
    if d["source_mismatches"]:
        print("   !! SOURCE MISMATCH:", d["source_mismatches"][:5])
    print()
