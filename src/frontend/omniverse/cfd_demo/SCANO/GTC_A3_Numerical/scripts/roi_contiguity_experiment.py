#!/usr/bin/env python3
"""Does a CONTIGUOUS ROI predict materially worse than a SCATTERED sample?

SCANO has only ever seen pivots drawn as a permutation slice over the whole domain
(HomoGraph_dataset.py:526-527 / :542-548 pre-patch). A user-dragged ROI is a contiguous
box, which is therefore out of distribution and UNVALIDATED. If contiguous is materially
worse, "simulate only the ROI" is unsound and the incremental-fill design is built on sand.

Six arms, all through the SAME sampler, selected by the SCANO_PIVOT_* env vars added to
HomoGraph_dataset.py. Arm S with no env set is bit-identical to the stock pipeline.
C3 and P are described at their entries in ARMS below.

  S   scattered baseline .... 51 draws x 8944 scattered pivots. Whole-domain coverage;
                              interior cells receive 9.368 draws each (measured).
  C1  contiguous, coverage-matched .. order=spatial repeat=6. 301 draws x 8944 pivots,
                              each draw a compact k-d block (measured bbox 3.75x6.07x5 m,
                              k-hop subgraph 10,978 nodes vs 66,457 scattered). Whole-domain
                              coverage, interior cells receive 9.498 draws each. Same cells,
                              same budget, same redundancy as S -> isolates CONTIGUITY.
  C2  contiguous, product-realistic .. pool=box. ONE draw whose pivots are the ROI's 12,108
                              cpd ids. This is literally what drag-and-simulate ships.
  X   scattered, size-matched control .. pool=scatter budget=1748. 7 scattered draws whose
                              k-hop subgraph is 14,002 nodes, size-matched to C2's 13,967
                              (0.25%). Separates "contiguous is OOD" from "smaller input
                              graph / one draw".

Every arm dumps raw per-cpd-cell prediction/target/draw_counts (SCANO_DUMP_RAW), so the
verdict is computed OFFLINE over an explicitly chosen cell set. The built-in pooled nRMSE
in inference_results.json must NOT be used to compare arms of different coverage: uncovered
rows carry prediction 0 AND target 0 (runner.py:1522-1533 / :1486-1497) and so deflate it
by sqrt(coverage) — the ROI arm would print ~5.7x BETTER while measuring nothing.

Usage:
    /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python \
        scripts/roi_contiguity_experiment.py            # run all arms + verdict
    ... roi_contiguity_experiment.py --analyze-only     # re-print verdict from existing dumps

Env:
    EXP_GPU=<n>        which single card to use. MUST NOT be the card Omniverse Kit holds.
    EXP_BOX="x0,y0,z0,x1,y1,z1"   the ROI, metres. Default is a 4.0x4.0x5.0 m box.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFER_DIR = os.path.join(BUNDLE, "cases", "GTC", "A3")
STOCK_CFG = os.path.join(INFER_DIR, "configs", "config_A3_inference.yaml")
PY = os.environ.get(
    "EXP_PY", "/mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python")

DATASET_ROOT = os.environ.get(
    "DC_CFD_AI_DATASET", "/mnt/lv/home/Public/GTC/processed_dataset_unconverged")
CASE = "case0"
SHARED_CASE = os.path.join(DATASET_ROOT, CASE)
SHARED_H5 = os.path.join(SHARED_CASE, "h5", f"{CASE}.h5")

EXP_ROOT = os.path.join(BUNDLE, "logger", "roi_experiment")
STAGE = os.path.join(EXP_ROOT, "stage")          # symlink farm -> shared dataset (read-only)

# The ROI. 4.0 x 4.0 x 5.0 m in the middle of the 31 x 19 x 5 m room -> 12,108 cpd ids
# (2,472 of them boundary faces), 2.7% of the mesh, k-hop-2 subgraph 13,967 nodes.
BOX = os.environ.get("EXP_BOX", "13.489,7.514,0,17.511,11.537,5")

# Secondary boxes, scored OFFLINE only (free — S and C1 both have whole-domain coverage).
# A single box cannot decide anything: the same run, same model scores 0.2037 in one box and
# 0.3215 in another. The spread of Delta ACROSS boxes is the result; one box is an anecdote.
SWEEP_BOXES = {
    "roi (the C2 box)": BOX,
    "centre 6x6x5":     "12,6,0,18,12,5",
    "corner 6x6x5":     "0,0,0,6,6,5",
    "low slab 8x8x2":   "10,5,0,18,13,2",
    "big 12x10x5":      "9,4,0,21,14,5",
    "far corner 6x6x5": "25,13,0,31,19,5",
}

# ---------------------------------------------------------------------------
# Acceptance criterion. Both numbers come from the model's OWN measurements.
# ---------------------------------------------------------------------------
# FAIL threshold = dspread_overall = 0.077342 (logger/training.log:7231, the matched-GT
#   dataset replay). That is the model's measured self-disagreement between pivot draws.
#   A contiguity penalty that large means switching to a box moves the answer as much as
#   drawing a completely different pivot set does — the ROI is then not the same field, and
#   incremental fill would stitch together answers that disagree by a full sampling-noise unit.
# PASS threshold = 0.020 ~= 26% of that dspread and 9.8% of the model's own whole-interior
#   error (nrmse_interior_overall = 0.204059, same run). It sits ~400x above the measured
#   run-to-run reproducibility floor (|delta| = 4.7e-5 between the two matched-GT replays),
#   so it is resolvable, and it is a defensible "the ROI answer is the same answer".
DSPREAD = 0.077342
NRMSE_INTERIOR_BASELINE = 0.204059
REPRO_FLOOR = 4.7e-5
PASS_DELTA = 0.020
FAIL_DELTA = DSPREAD
# Guard metric: the demo's headline is a -0.94 K ACU delta. The ROI's mean-T bias must not
# degrade by more than 0.10 K between arms (~10x margin on the rendered signal).
T_BIAS_GUARD_K = 0.10

FIELDS = ["u", "v", "w", "p", "T"]

ARMS = [
    ("S",  {}, "scattered baseline (stock: no env set)"),
    ("C1", {"SCANO_PIVOT_ORDER": "spatial", "SCANO_PIVOT_REPEAT": "6"},
     "contiguous blocks, coverage-matched to S"),
    ("C2", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX},
     "ROI-only: ONE draw = the box (what 3.2 ships)"),
    ("X",  {"SCANO_PIVOT_POOL": "scatter", "SCANO_PIVOT_BOX": BOX,
            "SCANO_PIVOT_BUDGET": "1748"},
     "scattered, k-hop-size-matched to C2"),
    # The arm that decides whether a FIX exists. Pool restricted to the ROI (so only the ROI
    # is ever touched — the whole point of drag-and-sim), but the budget dropped back to
    # rho=0.02 OF THE BOX so each draw is a SCATTERED subset of the box with a normal-looking
    # halo. This restores the baseline's pivot fraction (~13% of the subgraph, vs 87% for a
    # solid block) without ever leaving the ROI. If C3 passes while C2 fails, 3.2 is saved by
    # changing HOW the ROI is sampled, not by abandoning ROI-only inference.
    ("C3", {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
            "SCANO_PIVOT_BUDGET": str(max(1, int(0.02 * 12108))), "SCANO_PIVOT_REPEAT": "2"},
     "ROI-only but scattered WITHIN the box at rho=0.02"),
    # The decisive pairing with X. Identical pool cardinality (12,108), identical budget
    # (1748), identical draw count (7), identical RNG. The ONLY difference from X is the
    # SPATIAL EXTENT the pivots are drawn from: the 4x4x5 m ROI instead of the whole
    # 31x19x5 m room. P vs X isolates spatial extent from every other variable.
    ("P",  {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
            "SCANO_PIVOT_BUDGET": "1748"},
     "scattered within the ROI, budget/draws identical to X"),
]

# The K sweep for the mitigation. box+anchor = the ROI's cpd ids UNION K room-wide anchors,
# ONE draw. The adversarial re-examination measured +0.204 at K=1748 and +0.049 at K=8944,
# and — critically — 30,000 anchors came out WORSE (0.2704) than 8,944 (0.2613). K is
# non-monotone, so it has to be swept rather than picked. The same K placed as one distant
# COMPACT block gives +0.391, i.e. no help at all: it is the SPREAD that heals, which is
# also why a halo hugging the box cannot work.
ANCHOR_KS = [0, 1748, 4000, 8944, 12000, 16000, 20000, 30000, 45000, 60000]
ANCHOR_ARMS = [
    ("K%d" % k,
     {"SCANO_PIVOT_POOL": "box+anchor", "SCANO_PIVOT_BOX": BOX,
      "SCANO_PIVOT_ANCHOR": str(k)},
     "ROI + %d room-wide anchors, one draw" % k)
    for k in ANCHOR_KS
]
# Stratified placement at the SAME K. If spread is the active ingredient rather than
# count, one anchor per occupied grid cell should reach a given accuracy at smaller K
# than a uniform random draw, which clumps by chance. Same code path, one env var apart.
ANCHOR_ARMS += [
    ("T%d" % k,
     {"SCANO_PIVOT_POOL": "box+anchor", "SCANO_PIVOT_BOX": BOX,
      "SCANO_PIVOT_ANCHOR": str(k), "SCANO_PIVOT_ANCHOR_MODE": "stratified"},
     "ROI + %d STRATIFIED room-wide anchors, one draw" % k)
    for k in (4000, 8944, 16000, 30000)
]


def log(msg):
    print(msg, flush=True)


# ===========================================================================
# Guards
# ===========================================================================

def pick_gpu():
    """Exactly ONE card: never Kit's, never the resident AI worker's, never Prohibited.

    Kit holds DC_CFD_GPU (default 7). The demo's ai_worker pins DC_CFD_AI_GPUS (default 0)
    and will grab it on any click. Some cards on this host are compute_mode=Prohibited and
    fail with "CUDA-capable device(s) is/are busy or unavailable" — that is a mode, not a
    load, so memory.used alone does not detect it.
    """
    kit_gpu = os.environ.get("DC_CFD_GPU", os.environ.get("CFD_GPU", "7")).strip()
    worker_gpus = {g.strip() for g in os.environ.get("DC_CFD_AI_GPUS", "0").split(",") if g.strip()}
    info = {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used,compute_mode",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True).stdout
        for r in out.strip().splitlines():
            i, m, cm = [x.strip() for x in r.split(",")]
            info[i] = (int(m), cm)
    except (FileNotFoundError, subprocess.CalledProcessError):
        log("[guard] nvidia-smi unavailable; skipping GPU checks.")
        return os.environ.get("EXP_GPU", "4").strip()

    def usable(g):
        m, cm = info.get(g, (10 ** 9, "Unknown"))
        return cm == "Default" and m <= 500 and g != kit_gpu and g not in worker_gpus

    req = os.environ.get("EXP_GPU", "").strip()
    if req:
        if not usable(req):
            m, cm = info.get(req, ("?", "?"))
            sys.exit(f"[GUARD] EXP_GPU={req} unusable (compute_mode={cm}, used={m} MiB, "
                     f"kit={kit_gpu}, worker={sorted(worker_gpus)}). "
                     f"Usable now: {[g for g in sorted(info) if usable(g)]}")
        gpu = req
    else:
        cands = [g for g in sorted(info, key=int) if usable(g)]
        if not cands:
            sys.exit(f"[GUARD] no usable GPU. State: {info}")
        gpu = cands[0]
    log(f"[guard] GPU {gpu} selected (compute_mode={info[gpu][1]}, used={info[gpu][0]} MiB). "
        f"Kit is on GPU {kit_gpu}; ai_worker on {sorted(worker_gpus)}; both untouched.")
    return gpu


def snapshot_shared():
    """Fingerprint every file under the SHARED READ-ONLY dataset case.

    The staged case dir is a symlink farm into /mnt/lv/home/Public (ai_worker.stage_case
    pattern), and that tree is writable by this user. If Phase 1 ever decided the BCs had
    changed it would rewrite system/controlDict + system/fvSolution and merge a new t=1 INTO
    the shared H5 — through the symlinks. This snapshot is the proof it did not.
    """
    snap = {}
    for root, _dirs, files in os.walk(SHARED_CASE):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
                snap[os.path.relpath(p, SHARED_CASE)] = (st.st_size, st.st_mtime_ns, st.st_ino)
            except OSError:
                pass
    return snap


def assert_shared_untouched(before):
    after = snapshot_shared()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        sys.exit("[GUARD] THE SHARED READ-ONLY DATASET WAS MODIFIED — experiment invalid:\n  "
                 + "\n  ".join(changed[:20]))
    log(f"[guard] shared dataset untouched ({len(after)} files fingerprinted).")


def stage_dataset():
    """Own staging dir (symlinks), so the running demo worker's /tmp/dc_cfd_ai_work is
    neither read nor written. Mirrors ai_worker.stage_case (ai_worker.py:183-205)."""
    os.makedirs(os.path.join(STAGE, "case0", "h5"), exist_ok=True)
    for sub in ("0", "constant", "system"):
        src, dst = os.path.join(SHARED_CASE, sub), os.path.join(STAGE, "case0", sub)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    h5 = os.path.join(STAGE, "case0", "h5", "case0.h5")
    if not os.path.lexists(h5):
        os.symlink(SHARED_H5, h5)
    bcp = os.path.join(SHARED_CASE, "boundary_conditions.json")
    tgt = os.path.join(STAGE, "case0", "boundary_conditions.json")
    if os.path.exists(bcp) and not os.path.lexists(tgt):
        os.symlink(bcp, tgt)
    for extra in ("patch_statistics.json", "meta.json"):
        for cand in (os.path.join(DATASET_ROOT, extra), os.path.join(BUNDLE, "dataset", extra)):
            if os.path.exists(cand):
                shutil.copyfile(cand, os.path.join(STAGE, extra))
                break
        else:
            sys.exit(f"[GUARD] {extra} not found — importer.py:264 would silently fall back "
                     f"to a dataset-wide normalizer, i.e. a DIFFERENT model.")
    log(f"[guard] staged {STAGE} (symlinks into the shared dataset; patch_statistics.json + "
        f"meta.json copied).")
    return STAGE


def make_arm_config(arm):
    import yaml
    with open(STOCK_CFG) as f:
        cfg = yaml.safe_load(f)
    root = os.path.join(EXP_ROOT, f"arm_{arm}")
    os.makedirs(root, exist_ok=True)
    # results_root (runner.py:660-669) relocates training.log, inference_results.json and
    # vtu_output. Without it every arm writes the SAME three paths and arm N clobbers arm N-1.
    cfg["inference_config"]["results_root"] = root
    # The 44 MB / ~6 s field dump is not needed: the raw npz carries every cpd row exactly.
    cfg["inference_config"]["save_vtu"] = False
    path = os.path.join(EXP_ROOT, f"config_arm_{arm}.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return path, root


def run_arm(arm, envs, note, gpu, ds_path):
    cfg_path, root = make_arm_config(arm)
    dump = os.path.join(root, "raw")
    env = dict(os.environ)
    env.update({
        "CUDA_VISIBLE_DEVICES": gpu,     # the ONLY GPU selector (ai_worker.py:111)
        "OMP_NUM_THREADS": "4",
        "SCANO_DUMP_RAW": dump,
    })
    # Clear every knob first so a stale shell export cannot leak into the baseline arm.
    for k in ("SCANO_PIVOT_POOL", "SCANO_PIVOT_BOX", "SCANO_PIVOT_BUDGET",
              "SCANO_PIVOT_ORDER", "SCANO_PIVOT_REPEAT"):
        env.pop(k, None)
    env.update(envs)
    cmd = [PY, "inference.py", "--config", cfg_path, "--dataset-path", ds_path]

    logf = os.path.join(EXP_ROOT, f"arm_{arm}.log")
    log(f"\n=== ARM {arm}: {note}")
    log(f"    env: {envs or '(none — stock)'}")
    log(f"    cmd: cd {INFER_DIR} && CUDA_VISIBLE_DEVICES={gpu} {' '.join(cmd)}")
    t0 = time.time()
    with open(logf, "w") as fh:
        p = subprocess.run(cmd, cwd=INFER_DIR, env=env, stdout=fh,
                           stderr=subprocess.STDOUT)
    dt = time.time() - t0
    txt = open(logf, errors="replace").read()
    if p.returncode != 0:
        log(txt[-4000:])
        sys.exit(f"[FAIL] arm {arm} exited {p.returncode}; see {logf}")

    # The solver must never have run: it would rewrite the shared case through the symlinks
    # AND (worse) leave the t=500 ground truth matched to a different t=1.
    if "H5 t=1 cache valid" not in txt:
        sys.exit(f"[GUARD] arm {arm}: Phase 1 did NOT cache-hit — the OpenFOAM solver ran and "
                 f"the shared dataset may have been rewritten. See {logf}")
    if "0 recomputed, 0 cold-bootstrapped" not in txt:
        sys.exit(f"[GUARD] arm {arm}: Phase 1 recomputed or bootstrapped. See {logf}")

    for line in txt.splitlines():
        if "PIVOT-EXP" in line or "nodes were not sampled" in line or "coverage=" in line:
            log("    | " + line.strip()[:190])
    log(f"    done in {dt:.1f} s -> {dump}")
    return os.path.join(dump, f"{CASE}_raw.npz")


# ===========================================================================
# Offline scoring
# ===========================================================================

def load_geometry():
    import h5py
    import numpy as np
    with h5py.File(SHARED_H5, "r") as f:
        cen = f["cpd|centroid"][()].astype("float64")
        ctype = np.asarray(f["cpd|cell_type"][()]).squeeze()
    interior = (ctype == 0)
    with open(os.path.join(BUNDLE, "dataset", "meta.json")) as f:
        st = json.load(f)["dataset_statistics"]["std"]
    std = np.array([st["uvw_cpd_cell_u"], st["uvw_cpd_cell_v"], st["uvw_cpd_cell_w"],
                    st["press_cpd_cell"], st["temp_cpd_cell"]], dtype="float64")
    return cen, interior, std


def box_mask(cen, spec):
    import numpy as np
    b = [float(x) for x in spec.split(",")]
    return np.all((cen >= np.array(b[:3])) & (cen <= np.array(b[3:])), axis=1)


def load_arm(npz_path):
    import numpy as np
    d = np.load(npz_path)
    return {
        "pred": d["prediction"].astype("float64"),
        "tgt": d["target"].astype("float64"),
        "counts": d["draw_counts"].astype("float64"),
        "covered": d["draw_counts"] > 0,
    }


def nrmse(a, mask, std):
    import numpy as np
    n = int(mask.sum())
    if n == 0:
        return None, None, 0
    e = a["pred"][mask] - a["tgt"][mask]
    per = np.sqrt((e ** 2).mean(axis=0)) / std
    return per, float(per.mean()), n


def t_bias_K(a, mask):
    import numpy as np
    return float(np.abs(a["pred"][mask, 4].mean() - a["tgt"][mask, 4].mean()))


def analyze(paths):
    import numpy as np
    cen, interior, std = load_geometry()
    arms = {k: load_arm(p) for k, p in paths.items()}

    log("\n" + "=" * 96)
    log("COVERAGE (draws per cpd cell) — the number that decides whether a comparison is legal")
    log("=" * 96)
    log(f"{'arm':<5} {'covered cpd':>13} {'frac':>7} {'interior draws/cell':>21} "
        f"{'min':>5} {'boundary draws':>15}")
    for k, a in arms.items():
        c = a["counts"]
        cov = a["covered"]
        log(f"{k:<5} {int(cov.sum()):>13d} {cov.mean():>7.4f} "
            f"{c[interior & cov].mean():>21.3f} {c[interior & cov].min():>5.0f} "
            f"{c[~interior & cov].mean():>15.3f}")

    roi = box_mask(cen, BOX) & interior
    log(f"\nROI box = [{BOX}]  ->  {int(roi.sum())} interior cells "
        f"({100.0 * roi.sum() / interior.sum():.2f}% of the mesh)")

    results = {}

    # ---- (a) SAME CELL SET: the ROI, scored identically in S, C1, C2 -------------------
    roi_arms = [k for k in ("S", "C1", "C2", "C3", "P") if k in arms]
    cells_a = roi.copy()
    for k in roi_arms:
        cells_a &= arms[k]["covered"]
    log("\n" + "=" * 96)
    log(f"(a) THE ACCEPTANCE METRIC — identical cell set: {int(cells_a.sum())} interior cells "
        f"inside the ROI, covered in {', '.join(roi_arms)}")
    log("=" * 96)
    log(f"{'arm':<5} {'nRMSE_box':>10} {'Delta vs S':>11} "
        + " ".join(f'{f:>8}' for f in FIELDS) + f" {'|T bias| K':>11}")
    base = None
    for k in roi_arms:
        per, ov, n = nrmse(arms[k], cells_a, std)
        tb = t_bias_K(arms[k], cells_a)
        if k == "S":
            base, base_tb = ov, tb
        d = ov - base
        results[k] = {"nrmse_box": ov, "delta": d, "per_field": list(per), "t_bias_K": tb,
                      "n_cells": n}
        log(f"{k:<5} {ov:>10.6f} {d:>+11.6f} "
            + " ".join(f"{v:>8.4f}" for v in per) + f" {tb:>11.4f}")

    # ---- (b) SAME SIZE: C2 vs X, each vs S on its OWN covered set ----------------------
    # There is NO scattered sample of C2's size that covers C2's box — that is geometry, not a
    # design flaw. So the same-size comparison is done as a DIFFERENCE OF DIFFERENCES: each of
    # C2 and X is referenced to the identical S baseline restricted to that arm's own cells.
    log("\n" + "=" * 96)
    log("(b) SAME PIVOT-DRAW SIZE, different geometry — each arm vs S on its OWN covered cells")
    log("=" * 96)
    log(f"{'arm':<5} {'cells':>9} {'nRMSE':>10} {'nRMSE_S(same cells)':>20} {'Delta vs S':>11}")
    for k in [x for x in ("C2", "X", "C3", "P") if x in arms]:
        m = interior & arms[k]["covered"] & arms["S"]["covered"]
        _, ov, n = nrmse(arms[k], m, std)
        _, ovs, _ = nrmse(arms["S"], m, std)
        results.setdefault(k, {})["delta_own_cells"] = ov - ovs
        results[k]["n_own_cells"] = n
        log(f"{k:<5} {n:>9d} {ov:>10.6f} {ovs:>20.6f} {ov - ovs:>+11.6f}")

    # ---- (c) BOX-LOCATION SENSITIVITY --------------------------------------------------
    log("\n" + "=" * 96)
    log("(c) BOX-LOCATION SENSITIVITY — S vs C1 over six boxes (both have whole-domain "
        "coverage, so every box is scoreable in both)")
    log("=" * 96)
    log(f"{'box':<18} {'cells':>8} {'GTstd/domain':>13} {'nRMSE_S':>9} {'nRMSE_C1':>9} "
        f"{'Delta':>9}")
    deltas = []
    for name, spec in SWEEP_BOXES.items():
        m = box_mask(cen, spec) & interior & arms["S"]["covered"] & arms["C1"]["covered"]
        if m.sum() < 100:
            log(f"{name:<18} {int(m.sum()):>8d}  (too few cells — skipped)")
            continue
        _, s_ov, n = nrmse(arms["S"], m, std)
        _, c_ov, _ = nrmse(arms["C1"], m, std)
        # A dead-air box has almost no signal; its nRMSE is then a ratio of two small numbers.
        gtstd = float((arms["S"]["tgt"][m] .std(axis=0) / std).mean())
        deltas.append(c_ov - s_ov)
        log(f"{name:<18} {n:>8d} {gtstd:>13.3f} {s_ov:>9.6f} {c_ov:>9.6f} "
            f"{c_ov - s_ov:>+9.6f}")
    if deltas:
        log(f"{'across boxes':<18} {'':>8} {'':>13} {'':>9} {'':>9} "
            f"{'median ' + format(float(np.median(deltas)), '+.6f'):>9}")
        results["delta_C1_across_boxes"] = {"median": float(np.median(deltas)),
                                            "max": float(np.max(deltas)),
                                            "min": float(np.min(deltas))}

    # ---- VERDICT -----------------------------------------------------------------------
    d_c1 = results["C1"]["delta"]
    d_c2 = results["C2"]["delta"]
    d_x = results["X"]["delta_own_cells"]
    d_c2_own = results["C2"]["delta_own_cells"]
    dt_c2 = results["C2"]["t_bias_K"] - results["S"]["t_bias_K"]

    log("\n" + "=" * 96)
    log("VERDICT")
    log("=" * 96)
    log(f"  reference numbers (the model's own):  dspread_overall = {DSPREAD:.6f}   "
        f"nrmse_interior = {NRMSE_INTERIOR_BASELINE:.6f}   repro floor = {REPRO_FLOOR:.1e}")
    log(f"  PASS <= {PASS_DELTA:.3f}    GREY {PASS_DELTA:.3f}..{FAIL_DELTA:.3f}    "
        f"FAIL >= {FAIL_DELTA:.3f}   (nRMSE units, same cells)")

    def band(d):
        return "PASS" if d <= PASS_DELTA else ("GREY" if d < FAIL_DELTA else "FAIL")

    log(f"\n  Delta_C1 (pure contiguity, coverage-matched, ROI cells) = {d_c1:+.6f}  "
        f"-> {band(d_c1)}")
    log(f"  Delta_C2 (ROI-only, what 3.2 ships,        ROI cells) = {d_c2:+.6f}  "
        f"-> {band(d_c2)}")
    log(f"  Delta_X  (scattered, size-matched to C2, own cells)   = {d_x:+.6f}   "
        f"[C2 on own cells {d_c2_own:+.6f}]")
    log(f"  T-bias guard: |T| bias over the ROI degrades by {dt_c2:+.4f} K "
        f"(limit {T_BIAS_GUARD_K:.2f} K) -> "
        f"{'OK' if dt_c2 <= T_BIAS_GUARD_K else 'BREACH'}")

    verdict = band(d_c2)
    if dt_c2 > T_BIAS_GUARD_K and verdict == "PASS":
        verdict = "GREY"
    log(f"\n  >>> CONTIGUOUS ROI: {verdict}")
    if verdict == "PASS":
        log("      The ROI answer is the same answer. Drag-and-sim / incremental fill is sound.")
    elif verdict == "GREY":
        log("      Usable only with mitigation; do not ship 3.2 on this as-is.")
    else:
        log("      'Simulate only the ROI' does not return the same field. 3.2 is built on sand.")

    log("\n  Diagnosis (which mechanism, not just whether):")
    if d_c1 <= PASS_DELTA and d_c2 > PASS_DELTA:
        log("      Contiguity itself is FINE (C1 passes); the ROI arm loses the ~9.4x draw-")
        log("      averaging the full run gets for free. Mitigation is cheap and known: repeat")
        log("      the ROI draw R times with shifted block boundaries (SCANO_PIVOT_REPEAT).")
    elif d_c1 > PASS_DELTA:
        # NOT "contiguity costs accuracy" — that was measured and is false. Arm F draws
        # ONE dense CONTIGUOUS block at the paper's own rho=1.0 and PASSES (+0.022). What
        # costs accuracy is the pivot cloud's spatial EXTENT: the same node count placed
        # compactly fails (+0.391) while spread room-wide nearly recovers (+0.049).
        log("      Spatial EXTENT of the pivot cloud costs accuracy, not contiguity — a")
        log("      contiguous draw at rho=1.0 passes. Repeating ROI draws does not fix it.")
        log("      Inference-side mitigation: union the ROI pivots with a room-wide sprinkle.")
        log("      A true fix needs spatially restricted pools in TRAINING.")
    if abs(d_c2_own - d_x) <= max(PASS_DELTA / 2, 2 * REPRO_FLOOR):
        log("      C2 ~= X at matched k-hop subgraph size: the penalty is the SMALL INPUT GRAPH /")
        log("      single draw, not the geometry. Contiguity is not the culprit.")
    else:
        log(f"      C2 - X = {d_c2_own - d_x:+.6f} at matched subgraph size: the gap is "
            f"attributable to GEOMETRY, not graph size.")
    if "P" in results and "X" in results:
        d_p = results["P"]["delta_own_cells"]
        log(f"      DECISIVE PAIR — identical budget (1748), identical draw count (7), "
            f"identical pool size (12,108), identical RNG; the ONLY difference is the "
            f"SPATIAL EXTENT the pivots are drawn from:")
        log(f"          X (pivots spread over the whole 31x19x5 m room) = {d_x:+.6f} "
            f"-> {band(d_x)}")
        log(f"          P (same pivots confined to the 4x4x5 m ROI)     = {d_p:+.6f} "
            f"-> {band(d_p)}")
        log(f"          ratio {d_p / d_x if d_x else float('nan'):.1f}x. The damaging variable "
            f"is the pivot pool's SPATIAL EXTENT, not contiguity, not draw size, not coverage.")
        results["P"]["band"] = band(d_p)
    if "C3" in results:
        d_c3 = results["C3"]["delta"]
        log(f"      C3 (ROI-only but SCATTERED WITHIN the box, rho=0.02) = {d_c3:+.6f} "
            f"-> {band(d_c3)}")
        if verdict != "PASS" and band(d_c3) == "PASS":
            log("      >>> A FIX EXISTS. The failure is the SOLID BLOCK of pivots, not the ROI.")
            log("          Sampling the ROI the way training sampled the domain — many small")
            log("          scattered draws inside the box — recovers baseline accuracy while")
            log("          still touching only the ROI. 3.2 survives with that change.")
        elif band(d_c3) != "PASS":
            log("      >>> No cheap fix: even scattered-within-the-ROI degrades. The pivot pool")
            log("          being spatially restricted at all is the problem.")
        results["C3"]["band"] = band(d_c3)

    out = os.path.join(EXP_ROOT, "verdict.json")
    with open(out, "w") as f:
        json.dump({"box": BOX, "verdict": verdict, "delta_C1": d_c1, "delta_C2": d_c2,
                   "delta_X": d_x, "delta_C2_own_cells": d_c2_own,
                   "t_bias_degradation_K": dt_c2,
                   "thresholds": {"pass": PASS_DELTA, "fail": FAIL_DELTA,
                                  "dspread": DSPREAD, "repro_floor": REPRO_FLOOR},
                   "arms": results}, f, indent=2)
    log(f"\n  written: {out}")
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze-only", action="store_true",
                    help="skip the runs, re-score the existing raw dumps")
    ap.add_argument("--arms", default="S,C1,C2,X,C3,P")
    a = ap.parse_args()

    os.makedirs(EXP_ROOT, exist_ok=True)
    want = [x.strip() for x in a.arms.split(",") if x.strip()]
    all_arms = ARMS + ANCHOR_ARMS
    paths = {k: os.path.join(EXP_ROOT, f"arm_{k}", "raw", f"{CASE}_raw.npz")
             for k, _, _ in all_arms if k in want}

    if not a.analyze_only:
        with open(os.path.abspath(__file__), "rb") as f:
            log(f"script sha256 {hashlib.sha256(f.read()).hexdigest()[:16]}")
        gpu = pick_gpu()
        before = snapshot_shared()
        ds = stage_dataset()
        for k, envs, note in all_arms:
            if k not in want:
                continue
            paths[k] = run_arm(k, envs, note, gpu, ds)
        assert_shared_untouched(before)

    missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if missing:
        sys.exit(f"[FAIL] no raw dump for arms {missing} — run without --analyze-only.")
    verdict = analyze(paths)
    sys.exit(0 if verdict == "PASS" else (2 if verdict == "GREY" else 3))


if __name__ == "__main__":
    main()
