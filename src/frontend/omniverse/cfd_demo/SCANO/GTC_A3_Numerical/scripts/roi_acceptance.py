#!/usr/bin/env python3
"""Score a checkpoint on the ROI acceptance grid: 4 operating points x 2 boxes x 3 regimes.

Reuses the existing arm runner and scorer (scripts/roi_contiguity_experiment.py) so the
numbers are commensurable with every baseline measured with that script. Each operating
point is scored against ITS OWN whole-domain reference — using case0's reference for the
other three would silently inflate them.

Produces ``roi_acceptance.json`` with, per (box x operating point x regime): nrmse, delta,
band, coverage, a per-field breakdown, and |T bias|.

-------------------------------------------------------------------------------------------
Corrected interface (do NOT trust docs/drag-and-sim/06-retrain-plan.md Task 5's draft code
for the invocation half — it has three defects, verified against the real bundle):

  1. ``--raw-dump-dir`` does not exist. cases/GTC/A3/inference.py takes exactly three CLI
     args: --config, --checkpoint, --dataset-path. The raw dump is driven by the
     SCANO_DUMP_RAW=<dir> environment variable, read at
     src/Main/Data_driven/inferencer.py:576, written at :585.
  2. The npz path is ``<SCANO_DUMP_RAW>/<case_name>_raw.npz`` — there is no ``raw/``
     subdirectory unless SCANO_DUMP_RAW is itself pointed at one (which is what this
     script does: SCANO_DUMP_RAW = <arm_dir>/raw).
  3. SCANO_ACCEPT_ARM is invented in the draft and read nowhere in the source. Dropped.

A FOURTH defect, not called out anywhere, was found while wiring this up: the stock
config's ``inference_config.case`` is the fixed list ``["case0"]``, and inference.py's case
resolver looks up ``os.path.join(dataset_path, os.path.basename(case_entry))`` — i.e. it
always looks for a subdirectory literally named "case0" under whatever --dataset-path is
given. Pointing --dataset-path directly at ``<DS>/case100`` therefore makes it search for
``<DS>/case100/case0``, which does not exist, and inference.py raises "No valid cases found"
(verified by replaying the exact resolution loop against the stock YAML — see the task
report). The fix: --dataset-path is always DS (the parent containing every case), and a
per-operating-point config is generated with ``inference_config.case = [pt]`` so the
resolver looks for ``<DS>/<pt>``, which does exist.

-------------------------------------------------------------------------------------------
Read-only dataset safety. ``<DS>`` is a shared, read-only tree. cases/GTC/A3/inference.py's
Phase 1 (lines ~600-652) CAN write into a case directory — it temporarily rewrites
system/controlDict + system/fvSolution, deletes 1/, runs the solver, and merges a new t=1
into the case's H5 — if it decides the BC hash changed or no t=1 is cached. This script
never wants that to happen, so it:
  * refuses to run a point unless a preflight check confirms the BC hash matches AND t=1 is
    already cached (i.e. Phase 1 is certain to hit "H5 t=1 cache valid — skipping solver");
  * greps every run's log for that exact cache-hit line and for "0 recomputed, 0
    cold-bootstrapped", aborting loudly if either is missing;
  * fingerprints (size, mtime, inode) every file under each involved case directory before
    the grid starts and asserts nothing changed after it finishes.
None of this is a substitute for the dataset actually being read-only at the filesystem
level — it is a detector, not a lock — but it turns a silent corruption into a hard failure.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import h5py
import numpy as np
import yaml

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFER_DIR = os.path.join(BUNDLE, "cases", "GTC", "A3")
STOCK_CFG = os.path.join(INFER_DIR, "configs", "config_A3_inference.yaml")

# ---------------------------------------------------------------------------------------
# Verbatim constants from the task brief. Do not "correct" these.
# ---------------------------------------------------------------------------------------
DS = "/mnt/lv/home/Public/GTC/processed_dataset_unconverged"   # READ-ONLY. Never write here.
PY = "/mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python"
PASS_D, FAIL_D = 0.020, 0.077342

POINTS = ["case0", "case100", "case500", "case700"]
BOXES = {"study": "13.489,7.514,0,17.511,11.537,5",   # every prior baseline used this one
         "demo":  "8,12,0.5,16,17,3"}                  # the box a user actually drags
REGIMES = {"whole":   {},
           "roionly": {"SCANO_PIVOT_POOL": "box"},
           "k45000":  {"SCANO_PIVOT_POOL": "box+anchor", "SCANO_PIVOT_ANCHOR": "45000"}}

# The box IS a run axis for the box regimes, not only a scoring mask.
#
# An earlier version of this harness ran every box regime against the STUDY box and then
# applied both boxes as offline scoring masks, on the theory that the raw dump lets any
# cell subset be scored after the fact. That is true for `whole`, whose pivots cover the
# room, and it is what makes `whole` a legitimate per-point reference for BOTH boxes from a
# single run. It is false for the box regimes, and it cost four of the twenty-four cells:
#
#   * the two boxes are spatially DISJOINT (study y in [7.514, 11.537], demo y in [12, 17]),
#     so a roi-only run confined to the study box has ZERO covered cells inside the demo
#     box. All four demo/*/roionly cells came back n/a — and the demo box is the one a user
#     actually drags, so the most decision-relevant column was the empty one.
#   * `demo/*/k45000` did produce numbers, but they answered the wrong question: they scored
#     a run whose ROI was the STUDY box, on cells in the DEMO box, i.e. they measured how
#     well the 45,000 room-wide anchors happen to cover a region the ROI never targeted.
#     That is not "drag the demo box", which is what the cell's name claims.
#
# So each box regime runs once per (point, box): 4 points x (1 whole + 2 regimes x 2 boxes)
# = 20 inference calls. `whole` stays box-independent and is run once per point.
BOX_INDEPENDENT_REGIMES = ("whole",)

FIELDS = ["u", "v", "w", "p", "T"]

# Every key HomoGraph_dataset._parse_pivot_roi() reads. Cleared before every single arm
# (including "whole") so a stale shell export — or a leftover value from the previous arm's
# regime — can never leak into the next run and silently score the wrong thing.
_PIVOT_KEYS = ("SCANO_PIVOT_POOL", "SCANO_PIVOT_BOX", "SCANO_PIVOT_BUDGET",
               "SCANO_PIVOT_ORDER", "SCANO_PIVOT_REPEAT", "SCANO_PIVOT_ANCHOR",
               "SCANO_PIVOT_ANCHOR_MODE")

# GPU allowlist for this host (see task context): 0/7 belong to the Omniverse demo, 3 is a
# known-bad card (compute_mode Prohibited), 5 belongs to another user. Only 2/4/6 are ours.
CANDIDATE_GPUS = ("2", "4", "6")


def log(msg):
    print(msg, flush=True)


# =========================================================================================
# Read-only-dataset guards
# =========================================================================================

def _case_dir(pt):
    return os.path.join(DS, pt)


def preflight_cache_check(pt):
    """Refuse to run a point unless Phase 1 is certain to cache-hit (no solver, no writes)."""
    sys.path.insert(0, os.path.join(BUNDLE, "src"))
    from Utils.deployment_helpers import compute_bc_hash, read_stored_bc_hash

    case_path = _case_dir(pt)
    h5_path = os.path.join(case_path, "h5", f"{pt}.h5")
    if not os.path.isdir(case_path):
        sys.exit(f"[GUARD] {pt}: case dir not found at {case_path}")
    if not os.path.exists(h5_path):
        sys.exit(f"[GUARD] {pt}: h5 not found at {h5_path}")

    cur = compute_bc_hash(case_path)
    stored = read_stored_bc_hash(h5_path)
    if not cur or not stored or cur != stored:
        sys.exit(
            f"[GUARD] {pt}: BC-hash cache would NOT hit (current={cur[:12] or '<empty>'!r}, "
            f"stored={stored[:12] or '<empty>'!r}). Running would make Phase 1 invoke the "
            f"OpenFOAM solver and rewrite files under the READ-ONLY dataset at {case_path}. "
            f"Refusing to run — investigate before retrying."
        )
    with h5py.File(h5_path, "r") as f:
        tl = f.attrs.get("time_list", None)
        times = [float(t) for t in tl] if tl is not None else []
    if not times or abs(times[0] - 1.0) > 1e-6:
        sys.exit(f"[GUARD] {pt}: H5 has no t=1 cached (time_list={times}); Phase 1 would "
                 f"invoke the solver. Refusing to run.")
    log(f"[guard] {pt}: BC-hash cache-hit confirmed, t=1 present — Phase 1 will skip the solver.")


def snapshot_case(pt):
    """Fingerprint every file under a case directory (cheap: stat only, ~1-2k files)."""
    root = _case_dir(pt)
    snap = {}
    for cur, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(cur, fn)
            try:
                st = os.stat(p)
                snap[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, st.st_ino)
            except OSError:
                pass
    return snap


def assert_untouched(before, pt):
    after = snapshot_case(pt)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        sys.exit(f"[GUARD] {pt}: THE SHARED READ-ONLY DATASET WAS MODIFIED — harness result "
                 f"is invalid:\n  " + "\n  ".join(changed[:20]))
    log(f"[guard] {pt}: shared dataset untouched ({len(after)} files fingerprinted).")


# =========================================================================================
# GPU selection
# =========================================================================================

def pick_gpu(requested=None):
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used,compute_mode",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        sys.exit(f"[GUARD] nvidia-smi unavailable ({e}); refusing to guess a GPU. "
                 f"Pass --gpu explicitly if you are certain it is free.")
    info = {}
    for r in out.strip().splitlines():
        i, m, cm = [x.strip() for x in r.split(",")]
        info[i] = (int(m), cm)

    def usable(g):
        m, cm = info.get(g, (10 ** 9, "Unknown"))
        return cm == "Default" and m <= 500

    if requested:
        if requested not in CANDIDATE_GPUS:
            sys.exit(f"[GUARD] --gpu {requested} is outside the allowed set {CANDIDATE_GPUS} "
                     f"(0/7=Omniverse demo, 3=known-bad/Prohibited, 5=another user).")
        if not usable(requested):
            m, cm = info.get(requested, ("?", "?"))
            sys.exit(f"[GUARD] --gpu {requested} unusable (compute_mode={cm}, used={m} MiB).")
        gpu = requested
    else:
        cands = [g for g in CANDIDATE_GPUS if usable(g)]
        if not cands:
            sys.exit(f"[GUARD] none of the allowed GPUs {CANDIDATE_GPUS} are usable right now: "
                     f"{ {g: info.get(g) for g in CANDIDATE_GPUS} }")
        gpu = cands[0]
    log(f"[guard] GPU {gpu} selected (compute_mode={info[gpu][1]}, used={info[gpu][0]} MiB). "
        f"Allowed set {CANDIDATE_GPUS} — 0/7 (Omniverse), 3 (known-bad), 5 (another user) "
        f"untouched.")
    return gpu


# =========================================================================================
# Per-operating-point config + arm runner
# =========================================================================================

def make_point_config(pt, workdir):
    """Generate a config that fixes the fourth defect: override inference_config.case so
    the resolver looks for <DS>/<pt> instead of the stock config's hardcoded <DS>/case0."""
    with open(STOCK_CFG) as f:
        cfg = yaml.safe_load(f)
    cfg["inference_config"]["case"] = [pt]
    root = os.path.join(workdir, pt)
    os.makedirs(root, exist_ok=True)
    # results_root (runner.py:661-670) relocates training.log / inference_results.json /
    # vtu_output so sequential points don't clobber each other's logs.
    cfg["inference_config"]["results_root"] = os.path.join(root, "results")
    # The VTU field dump is not needed: the raw npz carries every cpd row exactly, and it
    # costs several seconds per run across 12 runs.
    cfg["inference_config"]["save_vtu"] = False
    path = os.path.join(root, "config.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return path


def run_arm(pt, rname, regime_env, checkpoint, workdir, cfg_path, gpu, force=False,
            box=None, bname=None):
    # Box regimes get one arm directory per (regime, box); `whole` keeps its box-free name
    # so a single run serves as the reference for every box.
    arm_key = rname if bname is None else f"{rname}__{bname}"
    arm_dir = os.path.join(workdir, pt, arm_key)
    dump_dir = os.path.join(arm_dir, "raw")
    os.makedirs(dump_dir, exist_ok=True)
    npz_path = os.path.join(dump_dir, f"{pt}_raw.npz")
    marker = os.path.join(arm_dir, "checkpoint_used.txt")

    if not force and os.path.exists(npz_path) and os.path.exists(marker):
        if open(marker).read().strip() == checkpoint:
            log(f"    [reuse] {pt}/{arm_key} -> {npz_path} (existing dump, same checkpoint)")
            return npz_path
        log(f"    [stale] {pt}/{arm_key}: existing dump used a different checkpoint — rerunning")

    env = dict(os.environ)
    for k in _PIVOT_KEYS:
        env.pop(k, None)
    env["CUDA_VISIBLE_DEVICES"] = gpu
    env["OMP_NUM_THREADS"] = env.get("OMP_NUM_THREADS", "4")
    env["SCANO_DUMP_RAW"] = dump_dir
    if regime_env:
        if box is None:
            raise ValueError(f"regime {rname!r} needs a box; none was passed")
        env["SCANO_PIVOT_BOX"] = box            # THIS cell's box, not a fixed study box
    env.update(regime_env)

    cmd = [PY, "inference.py", "--config", cfg_path, "--checkpoint", checkpoint,
           "--dataset-path", DS]
    logf = os.path.join(arm_dir, "log")
    log(f"\n=== RUN {pt}/{arm_key}   env={regime_env or '(whole domain — no SCANO_PIVOT_* set)'}")
    log(f"    cmd: cd {INFER_DIR} && CUDA_VISIBLE_DEVICES={gpu} {' '.join(cmd)}")
    t0 = time.time()
    with open(logf, "w") as fh:
        p = subprocess.run(cmd, cwd=INFER_DIR, env=env, stdout=fh, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    txt = open(logf, errors="replace").read()
    if p.returncode != 0:
        log(txt[-4000:])
        sys.exit(f"[FAIL] {pt}/{arm_key} exited {p.returncode}; see {logf}")

    # The solver must never have run for this arm — it would rewrite the shared case dir.
    if f"{pt}: H5 t=1 cache valid" not in txt:
        sys.exit(f"[GUARD] {pt}/{arm_key}: Phase 1 did NOT cache-hit — the OpenFOAM solver may "
                 f"have run against the shared read-only dataset. See {logf}")
    if "0 recomputed, 0 cold-bootstrapped" not in txt:
        sys.exit(f"[GUARD] {pt}/{arm_key}: Phase 1 recomputed or bootstrapped. See {logf}")
    if not os.path.exists(npz_path):
        sys.exit(f"[FAIL] {pt}/{arm_key}: expected raw dump not found at {npz_path}. See {logf}")

    with open(marker, "w") as f:
        f.write(checkpoint)
    for line in txt.splitlines():
        if "raw dump" in line or "coverage=" in line or "nodes were not sampled" in line:
            log("    | " + line.strip()[:190])
    log(f"    done in {dt:.1f}s -> {npz_path}")
    return npz_path


# =========================================================================================
# Offline scoring (std_vec / mask_for / score — as specified by the task brief)
# =========================================================================================

def std_vec():
    with open(os.path.join(BUNDLE, "dataset", "meta.json")) as f:
        st = json.load(f)["dataset_statistics"]["std"]
    return np.array([st["uvw_cpd_cell_u"], st["uvw_cpd_cell_v"], st["uvw_cpd_cell_w"],
                     st["press_cpd_cell"], st["temp_cpd_cell"]], dtype="float64")


def mask_for(case, box):
    """covered (elsewhere) ∩ interior ∩ box. interior = cpd|cell_type == 0; box is the
    centroid test on cpd|centroid, both read directly (read-only) from <DS>/<case>/h5."""
    with h5py.File(os.path.join(DS, case, "h5", f"{case}.h5"), "r") as f:
        cen = f["cpd|centroid"][()].astype("float64")
        ct = np.asarray(f["cpd|cell_type"][()]).squeeze()
    b = [float(x) for x in box.split(",")]
    in_box = np.all((cen >= np.array(b[:3])) & (cen <= np.array(b[3:])), axis=1)
    return in_box & (ct == 0)


def score(npz_path, mask, std):
    """Score covered ∩ mask. Always returns a dict — never None — so every (box, point,
    regime) grid cell gets an entry. coverage is reported as None (never 0.0) whenever
    nothing was scored, distinguishing 'no data' from a measured value."""
    d = np.load(npz_path)
    cov = d["draw_counts"] > 0
    denom = int(mask.sum())
    mm = mask & cov
    n_scored = int(mm.sum())
    coverage = (n_scored / denom) if denom > 0 else 0.0

    result = {
        "nrmse": None, "per_field": None, "t_bias_K": None,
        "coverage": None if coverage == 0 else coverage,
        "n_scored": n_scored, "n_cells": denom,
    }
    if n_scored == 0:
        return result

    pred = d["prediction"].astype("float64")[mm]
    tgt = d["target"].astype("float64")[mm]
    e = pred - tgt
    per = np.sqrt((e ** 2).mean(axis=0)) / std
    result["nrmse"] = float(per.mean())
    result["per_field"] = [float(x) for x in per]
    result["t_bias_K"] = float(abs(pred[:, 4].mean() - tgt[:, 4].mean()))
    return result


def band_of(delta):
    if delta is None:
        return None
    if delta <= PASS_D:
        return "PASS"
    if delta >= FAIL_D:
        return "FAIL"
    return "GREY"


# =========================================================================================
# Main
# =========================================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", required=True,
                    help="Path passed straight through to inference.py --checkpoint "
                         "(e.g. logger/model/best.state, resolved relative to the bundle).")
    ap.add_argument("--out", default="roi_acceptance.json")
    ap.add_argument("--workdir", default="/tmp/roi_acceptance")
    ap.add_argument("--gpu", default=None,
                    help="Force a specific GPU from the allowed set %s. Default: "
                         "auto-pick the first usable one." % (CANDIDATE_GPUS,))
    ap.add_argument("--points", default=",".join(POINTS),
                    help="Comma-separated subset of operating points to run "
                         "(e.g. 'case0' for a cheap 3-run validation before the full grid).")
    ap.add_argument("--regimes", default=",".join(REGIMES),
                    help="Comma-separated subset of regimes to run.")
    ap.add_argument("--force", action="store_true",
                    help="Re-run an arm even if a matching-checkpoint dump already exists "
                         "in --workdir.")
    a = ap.parse_args()

    points = [x.strip() for x in a.points.split(",") if x.strip()]
    regimes = [x.strip() for x in a.regimes.split(",") if x.strip()]
    for pt in points:
        if pt not in POINTS:
            sys.exit(f"[FAIL] unknown operating point {pt!r}; must be one of {POINTS}")
    for r in regimes:
        if r not in REGIMES:
            sys.exit(f"[FAIL] unknown regime {r!r}; must be one of {list(REGIMES)}")
    if "whole" not in regimes:
        sys.exit("[FAIL] the 'whole' regime is required — every delta is measured against "
                 "its own operating point's whole-domain reference.")

    os.makedirs(a.workdir, exist_ok=True)
    gpu = pick_gpu(a.gpu)
    std = std_vec()

    log(f"\n[plan] points={points}  regimes={regimes}  "
        f"({len(points) * len(regimes)} inference runs)")

    # ---- preflight: verify EVERY point will cache-hit before spending any GPU time -------
    for pt in points:
        preflight_cache_check(pt)

    before = {pt: snapshot_case(pt) for pt in points}

    # ---- run the grid (<= 20 inference calls: box-independent `whole` once per point, -----
    # ---- every box regime once per (point, box)) ------------------------------------------
    npz = {}
    for pt in points:
        cfg_path = make_point_config(pt, a.workdir)
        for rname in regimes:
            if rname in BOX_INDEPENDENT_REGIMES:
                npz[f"{pt}/{rname}"] = run_arm(
                    pt, rname, REGIMES[rname], a.checkpoint, a.workdir, cfg_path, gpu,
                    a.force)
                continue
            for bname, box in BOXES.items():
                npz[f"{bname}/{pt}/{rname}"] = run_arm(
                    pt, rname, REGIMES[rname], a.checkpoint, a.workdir, cfg_path, gpu,
                    a.force, box=box, bname=bname)

    for pt in points:
        assert_untouched(before[pt], pt)

    # ---- scoring: each cell is scored on its own box, against that point's whole run -------
    results = {}
    for bname, box in BOXES.items():
        for pt in points:
            m = mask_for(pt, box)
            ref = score(npz[f"{pt}/whole"], m, std)
            for rname in regimes:
                key = (f"{pt}/{rname}" if rname in BOX_INDEPENDENT_REGIMES
                       else f"{bname}/{pt}/{rname}")
                s = score(npz[key], m, std)
                entry = dict(s)
                entry["ref_nrmse"] = ref["nrmse"]
                if s["nrmse"] is not None and ref["nrmse"] is not None:
                    delta = s["nrmse"] - ref["nrmse"]
                    entry["delta"] = delta
                    entry["band"] = band_of(delta)
                else:
                    entry["delta"] = None
                    entry["band"] = None
                results[f"{bname}/{pt}/{rname}"] = entry

    out = {
        "checkpoint": a.checkpoint,
        "points": points,
        "regimes": regimes,
        "boxes": BOXES,
        "fields": FIELDS,
        "thresholds": {"pass_delta": PASS_D, "fail_delta": FAIL_D},
        "results": results,
    }
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2)

    log("\n" + "=" * 100)
    log(f"{'cell':<24} {'nrmse':>10} {'ref_nrmse':>10} {'delta':>10} {'band':>5} "
        f"{'coverage':>9} {'|T bias| K':>11}")
    log("=" * 100)
    for k in sorted(results):
        v = results[k]
        def fmt(x, spec):
            return format(x, spec) if x is not None else "n/a"
        log(f"{k:<24} {fmt(v['nrmse'], '10.6f'):>10} {fmt(v['ref_nrmse'], '10.6f'):>10} "
            f"{fmt(v['delta'], '+10.6f'):>10} {v['band'] or 'n/a':>5} "
            f"{fmt(v['coverage'], '9.4f'):>9} {fmt(v['t_bias_K'], '11.4f'):>11}")
    log(f"\nwritten: {a.out}")


if __name__ == "__main__":
    main()
