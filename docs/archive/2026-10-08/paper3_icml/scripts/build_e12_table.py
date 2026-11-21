#!/usr/bin/env python3
"""Generate the three-arm table Amendment 30.2c requires, and check the pasted one.

A30.2c commits to all three arms in the SAME table and to the mask counter being
reported whichever way it comes.  The table is generated from
`records/e12_agent_result.json` rather than typed, because every prose-carried number
in this campaign that was checked turned out to be wrong, and because a table whose
rows are typed cannot be checked against the run it describes.

    python scripts/build_e12_table.py            # print the LaTeX rows
    python scripts/build_e12_table.py --check    # fail unless main.tex carries them
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ARMS = ("ste_s0", "grpo_s0", "e12d_s0_r2")
ARM_LABEL = {
    "ste_s0": r"ste (coupled)",
    "grpo_s0": r"grpo (scalar)",
    "e12d_s0_r2": r"ste $+$ mask (E12d)",
}
SPLITS = ("val", "test", "ood")


def load(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"FAIL: no record at {p}; run extract_agent_result.py "
                         f"--arms={','.join(ARMS)} first")
    return json.loads(p.read_text())


def fmt(v, nd=4, signed=True):
    if v is None:
        return "---"
    return (f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}")


def rows(rec, step):
    """LaTeX rows for one evaluation step, one per arm."""
    out = []
    for arm in ARMS:
        a = rec["arms"].get(arm)
        if a is None:
            out.append(f"{ARM_LABEL[arm]} & \\multicolumn{{7}}{{l}}{{not in the record}} \\\\")
            continue
        blk = next((b for b in a["blocks"] if b["step"] == step), None)
        cells = []
        for sp in SPLITS:
            if blk is None or sp not in blk:
                cells += ["---", "---"]
            else:
                cells += [fmt(blk[sp]["regret_median"]),
                          f"{blk[sp]['feasible_rate']:.3f}"]
        mask = a.get("penalty_masked_samples_total")
        nlog = a.get("n_steps_logging_penalty_mask")
        if mask is None:
            maskcell = "n/a"
        else:
            maskcell = f"${mask}$ of ${nlog}$"
        cells.append(maskcell)
        out.append(f"{ARM_LABEL[arm]} & " + " & ".join(cells) + r" \\")
    return out


def caption_counts(rec):
    """The counters that must appear in the prose beside the table."""
    out = {}
    for arm in ARMS:
        a = rec["arms"].get(arm) or {}
        out[arm] = dict(
            steps=a.get("n_step_records"),
            mask=a.get("penalty_masked_samples_total"),
            logged=a.get("n_steps_logging_penalty_mask"),
            flag=a.get("pathwise_penalty_mask_flag"),
            jmean0=a.get("train_J_mean_over_steps_with_zero_mask"),
            jmax=a.get("train_J_mean_max"),
            done=a.get("done_record_present"),
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default=str(HERE / "records" / "e12_agent_result.json"))
    ap.add_argument("--tex", default=str(HERE / "main.tex"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--allow-pending", action="store_true",
                    help="pass with a loud note while the third arm is still running, so "
                         "this gate can have a call site before the run it gates exists.  "
                         "Without it, a two-arm record is a failure.")
    ap.add_argument("--step", type=int, default=None,
                    help="evaluation step to tabulate; default is the last one every "
                         "arm has, so a partial arm cannot be tabled beside a final one "
                         "without the difference being visible")
    args = ap.parse_args()

    rec = load(args.record)
    missing = [a for a in ARMS if a not in (rec.get("arms") or {})]
    if missing and args.allow_pending:
        print(f"e12 three-arm table PENDING: {', '.join(missing)} not in "
              f"{Path(args.record).name}, so A30.2c's table is not yet buildable. "
              f"This is a pass only because --allow-pending was given; the check becomes "
              f"strict the moment the record carries all three arms "
              f"({', '.join(ARMS)}).")
        return 0
    cc = caption_counts(rec)
    steps = {a: sorted({b["step"] for b in (rec["arms"].get(a) or {}).get("blocks", [])})
             for a in ARMS}
    common = set.intersection(*[set(v) for v in steps.values()]) if all(steps.values()) else set()
    if not common:
        print("FAIL: the three arms share no evaluation step, so there is no table to build. "
              f"steps per arm: {steps}", file=sys.stderr)
        return 2
    step = args.step if args.step is not None else max(common)
    if step != max(common):
        print(f"note: tabulating step {step}; the last step all three arms share is "
              f"{max(common)}", file=sys.stderr)

    body = rows(rec, step)
    print(f"% three-arm table at evaluation step {step}; generated by build_e12_table.py "
          f"from {Path(args.record).name} -- no number in it is typed")
    for ln in body:
        print(ln)

    print("\n% counters that must appear in the prose beside the table", file=sys.stderr)
    for arm, d in cc.items():
        print(f"%   {arm:<12} steps={d['steps']} mask={d['mask']} logged={d['logged']} "
              f"flag={d['flag']} final={d['done']}", file=sys.stderr)
        print(f"%                J_mean on zero-mask steps={d['jmean0']} max={d['jmax']}",
              file=sys.stderr)

    # ---- structural checks, independent of the LaTeX -----------------------
    problems = []
    if not rec.get("is_final"):
        problems.append("the record is a snapshot of a run still in flight "
                        f"(snapshot_of_steps={rec.get('snapshot_of_steps')}); A30.2c's table "
                        "is a table of completed arms")
    for arm in ARMS:
        a = rec["arms"].get(arm)
        if a is None:
            problems.append(f"{arm} is absent from the record, so the table is not three arms")
            continue
        if not a.get("done_record_present"):
            problems.append(f"{arm} has no `done` record, so it did not finish")
    d = cc.get("e12d_s0_r2") or {}
    if d.get("flag") is not True:
        problems.append("e12d_s0_r2's own args.json does not carry --pathwise_penalty_mask, so "
                        "it is not the arm Amendment 30.2 pre-registered and its counter "
                        "means nothing")
    if d.get("mask") is None or not d.get("logged"):
        problems.append("e12d_s0_r2 logged no penalty-mask counter, so the pre-registered "
                        "instrument did not run and the branch of A30.2c cannot be decided")
    # the other two arms must NOT look like they masked anything
    for arm in ("ste_s0", "grpo_s0"):
        a = cc.get(arm) or {}
        if a.get("mask") is not None:
            problems.append(f"{arm} reports a penalty-mask total of {a['mask']}, but it was not "
                            "launched with the flag; a counter on an arm that cannot mask is a "
                            "counter that means something other than what it says")

    if args.check:
        tex = Path(args.tex).read_text()
        for ln in body:
            probe = " ".join(ln.split())
            flat = " ".join(tex.split())
            if probe not in flat:
                problems.append(f"the generated row is not in main.tex: {probe[:110]}")
        if f"step~${step}$" not in tex.replace("\\ ", " ") and f"step ${step}" not in tex:
            problems.append(f"main.tex does not say which evaluation step the table is at "
                            f"({step}); a three-arm table with no stated step is a table whose "
                            "arms may not be compared")

    if problems:
        print("\nE12 TABLE GATE FAILED:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1
    print("\ne12 three-arm table agrees with the record and is present in main.tex",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
