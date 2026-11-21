#!/usr/bin/env python3
"""The two readouts the slice-basis experiment still owed: ENTROPY and COST.

A. ENTROPY — the direct readout of the proposed mechanism.
   The diagnosis says a confined sample collapses the slice basis (trunk entropy
   2.699 -> 2.286, ~14.9 -> ~9.8 effective slices of 32) and that injection repairs it.
   Two entropies are measured at every one of the 9 pooling sites, per forward:

     OCCUPANCY  H over the per-node assignment mass, p_g = (sum_n slice_weights[n,h,g]) / .
                This is the quantity the diagnosis reported. Injection does not write
                slice_weights directly, but it DOES move this — measured 2.2854 -> 2.4585.
                The trunk is 7 sequential layers, so replacing layer L's token changes
                layer L+1's input and therefore ITS pooling. Only layer 0 is invariant
                (3.46545 in both arms), which is the clean control. An earlier version of
                this comment predicted the opposite; the measurement is what stands.
     TOKEN      H over the pooled basis's energy share across slices, p_g = ||tok[h,g,:]||^2 / .
                This IS what injection replaces, so this is the readout that can move.

   Arms: whole-domain (S), ROI-only (C2), ROI+injected basis (same point and cross point),
   and the non-circular box+anchor K=45000 mitigation for reference.

B. COST — node-forwards and wall clock, read off the arms ALREADY run without the entropy
   instrumentation (entropy adds work inside the forward and would corrupt the timing).
   node-forwards = sum(draw_counts) over the raw dump: the number of per-node predictions
   the model actually produced, i.e. draws x k-hop subgraph size, measured not assumed.

Run:
    EXP_GPU=1 /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python \
        scripts/slice_entropy_and_cost.py
    ... --cost-only        # no GPU work: re-derive the cost table from existing dumps
    ... --analyze-only     # re-print both tables from existing dumps
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import roi_contiguity_experiment as roi          # noqa: E402
import slice_basis_experiment as sb              # noqa: E402

BOX = sb.BOX
EXP_ROOT = sb.EXP_ROOT
ENT_DIR = os.path.join(EXP_ROOT, "entropy")
BASIS_A = os.path.join(sb.BASIS_DIR, f"basis_{sb.CAPTURE_CASE}.pt")

ROI_PRIOR = os.path.join(BUNDLE, "logger", "roi_experiment")


def log(m):
    print(m, flush=True)


def ent_path(arm):
    return os.path.join(ENT_DIR, f"{arm}.json")


# --------------------------------------------------------------------------------------
# A. entropy arms
# --------------------------------------------------------------------------------------
# (arm, case, extra env, note)
ENT_ARMS = [
    ("ENT_S", "case0", {}, "whole-domain, 51 draws (the basis the ROI wants to borrow)"),
    ("ENT_C2", "case0", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX},
     "ROI-only, pools its own basis"),
    ("ENT_INJ", "case0", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
                          "SCANO_BASIS_INJECT": BASIS_A, "SCANO_BASIS_SITES": "all"},
     "ROI-only + basis_A, all 8 query sites  [SAME POINT — CIRCULAR]"),
    ("ENT_INJ_TRUNK", "case0", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
                                "SCANO_BASIS_INJECT": BASIS_A, "SCANO_BASIS_SITES": "trunk"},
     "ROI-only + basis_A, 7 trunk sites only  [SAME POINT — CIRCULAR]"),
    ("ENT_K45000", "case0", {"SCANO_PIVOT_POOL": "box+anchor", "SCANO_PIVOT_BOX": BOX,
                             "SCANO_PIVOT_ANCHOR": "45000"},
     "box + 45000 room-wide anchors (the non-circular mitigation)"),
    ("ENT_X700_C2", "case700", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX},
     "ROI-only @ case700"),
    ("ENT_X700_INJ", "case700", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
                                 "SCANO_BASIS_INJECT": BASIS_A, "SCANO_BASIS_SITES": "all"},
     "ROI-only @ case700 + basis_A(case0)  [CROSS POINT]"),
]


def run_entropy_arms(gpu):
    os.makedirs(ENT_DIR, exist_ok=True)
    for arm, case, envs, note in ENT_ARMS:
        e = dict(envs)
        e["SCANO_BASIS_ENTROPY"] = ent_path(arm)
        e.setdefault("SCANO_BASIS_LABEL", f"{arm}: {note}")
        sb.run_arm(arm, case, e, note, gpu)
        if not os.path.exists(ent_path(arm)):
            sys.exit(f"[FAIL] arm {arm} wrote no entropy file — the readout never fired.")


def analyze_entropy():
    rows = []
    for arm, case, envs, note in ENT_ARMS:
        p = ent_path(arm)
        if not os.path.exists(p):
            log(f"  (missing {arm})")
            continue
        b = json.load(open(p))
        sites = b["sites"]
        trunk = {k: v for k, v in sites.items() if not k.endswith((".internal", ".patch"))}
        enc = sites.get("encoder.internal", {})
        n = max(1, len(trunk))
        rows.append({
            "arm": arm, "case": case, "note": note,
            "n_forwards": b["sites"][list(sites)[0]]["n_forwards"],
            "trunk_occ": sum(v["occupancy_entropy"] for v in trunk.values()) / n,
            "trunk_tok_own": sum(v["token_entropy_own"] for v in trunk.values()) / n,
            "trunk_tok_used": sum(v["token_entropy_used"] for v in trunk.values()) / n,
            "enc_occ": enc.get("occupancy_entropy"),
            "enc_tok_own": enc.get("token_entropy_own"),
            "enc_tok_used": enc.get("token_entropy_used"),
            "per_site": {k: (v["token_entropy_own"], v["token_entropy_used"],
                             v.get("occupancy_entropy")) for k, v in sorted(sites.items())},
        })
    return rows


def print_entropy(rows):
    log("\n" + "=" * 118)
    log("A. SLICE ENTROPY  (nats; exp(H) = effective slices out of G=32; trunk = mean over "
        "the 7 AttnProcessor sites)")
    log("=" * 118)
    log(f"{'arm':<15}{'fwd':>5}  {'OCCUPANCY (assignment)':>24}  "
        f"{'TOKEN own (pooled here)':>25}  {'TOKEN used (after inject)':>27}")
    log(f"{'':<15}{'':>5}  {'H      eff':>24}  {'H      eff':>25}  {'H      eff':>27}")
    log("-" * 118)
    for r in rows:
        log(f"{r['arm']:<15}{r['n_forwards']:>5}  "
            f"{r['trunk_occ']:>14.4f}{math.exp(r['trunk_occ']):>10.2f}  "
            f"{r['trunk_tok_own']:>15.4f}{math.exp(r['trunk_tok_own']):>10.2f}  "
            f"{r['trunk_tok_used']:>17.4f}{math.exp(r['trunk_tok_used']):>10.2f}   {r['note']}")
    log("-" * 118)
    log("encoder.internal (the cross-attention Q site the diagnosis NAMES):")
    for r in rows:
        if r["enc_occ"] is None:
            continue
        log(f"  {r['arm']:<15} occ H={r['enc_occ']:.4f} ({math.exp(r['enc_occ']):.2f} eff) | "
            f"token H own={r['enc_tok_own']:.4f} -> used={r['enc_tok_used']:.4f} "
            f"({math.exp(r['enc_tok_own']):.2f} -> {math.exp(r['enc_tok_used']):.2f} eff)")


# --------------------------------------------------------------------------------------
# B. cost
# --------------------------------------------------------------------------------------
# (label, log file, raw dump) — all measured WITHOUT the entropy instrumentation.
COST_ARMS = [
    ("S   whole-domain, 51 draws (the baseline a drag-and-sim must beat)",
     os.path.join(EXP_ROOT, "arm_INERT_S.log"),
     os.path.join(EXP_ROOT, "arm_INERT_S", "raw", "case0_raw.npz")),
    ("C2  ROI-only, 1 draw (FAILS accuracy: +0.4051)",
     os.path.join(EXP_ROOT, "arm_INERT_C2.log"),
     os.path.join(EXP_ROOT, "arm_INERT_C2", "raw", "case0_raw.npz")),
    ("C2 + basis injection, all 8 sites  [CIRCULAR]",
     os.path.join(EXP_ROOT, "arm_MECH_ALL.log"),
     os.path.join(EXP_ROOT, "arm_MECH_ALL", "raw", "case0_raw.npz")),
    ("C2 + basis injection, 7 trunk sites  [CIRCULAR]",
     os.path.join(EXP_ROOT, "arm_MECH_TRUNK.log"),
     os.path.join(EXP_ROOT, "arm_MECH_TRUNK", "raw", "case0_raw.npz")),
    ("K45000  box + 45k anchors, 1 draw (the only non-circular pass)",
     os.path.join(ROI_PRIOR, "arm_K45000.log"),
     os.path.join(ROI_PRIOR, "arm_K45000", "raw", "case0_raw.npz")),
    ("CAP_A  whole-domain 51 draws + basis capture (the one-off the reuse needs)",
     os.path.join(EXP_ROOT, "arm_CAP_A.log"),
     os.path.join(EXP_ROOT, "arm_CAP_A", "raw", "case0_raw.npz")),
]


def parse_cost(logf, npz):
    import numpy as np
    out = {}
    txt = open(logf, errors="replace").read()
    m = re.search(r"DataLoader: (\d+) batches", txt)
    out["draws"] = int(m.group(1)) if m else None
    m = re.search(r"Total forward: ([\d.]+) ms", txt)
    out["fwd_ms"] = float(m.group(1)) if m else None
    m = re.search(r"Total data load: ([\d.]+) ms", txt)
    out["load_ms"] = float(m.group(1)) if m else None
    m = re.search(r"Inference time: ([\d.]+)s", txt)
    out["infer_s"] = float(m.group(1)) if m else None
    m = re.search(r"Per-batch forward times \(ms\): \[([^\]]*)\]", txt)
    if m:
        v = [float(x.strip().strip("'")) for x in m.group(1).split(",") if x.strip()]
        out["first_ms"] = v[0]
        # the trailing batch of a whole-domain run is a short remainder; the steady-state
        # cost per draw is the median of the batches after the CUDA warm-up.
        out["steady_ms"] = sorted(v[1:])[len(v[1:]) // 2] if len(v) > 2 else v[0]
    d = np.load(npz)
    dc = d["draw_counts"]
    out["node_forwards"] = int(dc.sum())
    out["cells_touched"] = int((dc > 0).sum())
    out["cells_total"] = int(dc.shape[0])
    return out


def print_cost():
    log("\n" + "=" * 118)
    log("C. COST  (node-forwards = sum of per-cell draw_counts = draws x k-hop subgraph "
        "size, MEASURED from the raw dumps)")
    log("=" * 118)
    log(f"{'arm':<62}{'draws':>6}{'node-fwd':>11}{'touched':>9}{'fwd ms':>9}{'infer s':>9}")
    log("-" * 118)
    base = None
    rows = []
    for label, logf, npz in COST_ARMS:
        if not (os.path.exists(logf) and os.path.exists(npz)):
            log(f"  (missing {label})")
            continue
        c = parse_cost(logf, npz)
        c["label"] = label
        rows.append(c)
        if base is None:
            base = c
        log(f"{label:<62}{c['draws']:>6}{c['node_forwards']:>11,}"
            f"{100.0 * c['cells_touched'] / c['cells_total']:>8.1f}%"
            f"{c['fwd_ms']:>9.1f}{c['infer_s']:>9.2f}")
    log("-" * 118)
    if base:
        log("speed-up vs the 51-draw whole-domain baseline:")
        for c in rows[1:]:
            log(f"  {c['label'][:58]:<58} node-forwards {base['node_forwards'] / c['node_forwards']:>6.1f}x"
                f"   GPU forward {base['fwd_ms'] / c['fwd_ms']:>5.1f}x"
                f"   end-to-end {base['infer_s'] / c['infer_s']:>5.1f}x")
    log("\nNOTE: every single-draw arm's forward time IS its cold first batch (CUDA warm-up "
        "included);\n      the 51-draw arm amortises one 230 ms warm-up over 50 x ~70 ms "
        "steady batches. Steady-state\n      per-draw cost is printed below for the arms "
        "that have more than one batch.")
    for c in rows:
        if c["draws"] and c["draws"] > 2:
            log(f"  {c['label'][:58]:<58} first {c['first_ms']:.1f} ms, steady median "
                f"{c['steady_ms']:.1f} ms/draw")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--cost-only", action="store_true")
    a = ap.parse_args()

    if not (a.analyze_only or a.cost_only):
        if not os.path.exists(BASIS_A):
            sys.exit(f"[FAIL] {BASIS_A} missing — run slice_basis_experiment.py --stages capture")
        gpu = roi.pick_gpu()
        cases = sorted({c for _, c, _, _ in ENT_ARMS})
        before = sb.snapshot(cases)
        run_entropy_arms(gpu)
        sb.assert_untouched(before, cases)

    rows = [] if a.cost_only else analyze_entropy()
    if rows:
        print_entropy(rows)
        with open(os.path.join(EXP_ROOT, "entropy_summary.json"), "w") as f:
            json.dump(rows, f, indent=2)
    cost = print_cost()
    with open(os.path.join(EXP_ROOT, "cost_summary.json"), "w") as f:
        json.dump(cost, f, indent=2)


if __name__ == "__main__":
    main()
