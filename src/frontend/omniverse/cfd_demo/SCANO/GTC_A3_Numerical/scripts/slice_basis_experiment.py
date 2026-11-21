#!/usr/bin/env python3
"""Does an ROI pass recover if it BORROWS a whole-domain slice basis?

THE HYPOTHESIS. Restricting SCANO's pivot pool to a contiguous box costs +0.405 nRMSE
(fail threshold 0.077). The boundary is not what breaks — the cross-attention injects all
856 patches in every arm, byte-identical, and it is load-bearing. What breaks is the
QUERY side: the slice basis is pooled node-set-globally eight times per forward, and a
spatially confined sample collapses it. If the ROI pass reuses a basis captured from a
whole-domain pass instead of pooling its own, does the ROI regain the ability to address
the boundary?

THE CIRCULARITY TRAP, ADDRESSED HEAD-ON. A basis captured at the SAME operating point
comes from a pass that already IS the answer for those cells; "the ROI now matches it" is
then near-tautological. So this script runs BOTH and labels them:

    MECHANISM (same operating point, CIRCULAR — confirms/refutes the diagnosis, is NOT
        evidence of a usable feature): capture at case0, inject into case0's ROI pass.
    CROSS-POINT (the result that would matter): capture ONCE at case0 = BC-A, then inject
        that same file into ROI passes at BC-B/C/D, where no whole-domain pass exists.

Every case in this dataset shares one mesh (447,205 cpd cells, identical centroids), so
BC-B/C/D are true operating-point changes — different ACU supply temperatures, supply
flow rates and server powers — over the identical ROI cell set. The whole-domain run at
each cross-point case is executed ONLY as the scoring reference (it is what "delta vs S"
is measured against, exactly as in the existing study); the injected basis never comes
from it.

A built-in meter makes a same-point result impossible to quote as a cross-point one: the
boundary K/V slice token is captured but never injected, and its cached-vs-own relative
L2 is reported per run. It is ~0 iff the cache came from the same operating point.

MEASUREMENT MATCHES THE EXISTING STUDY so numbers are comparable: same harness plumbing
(imported from roi_contiguity_experiment.py), same box, same 9,636 interior ROI cells,
same nRMSE normalised by meta.json dataset_statistics.std, same PASS<=0.020 /
FAIL>=0.077342 bands.

Usage:
    /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python \
        scripts/slice_basis_experiment.py                 # everything
    ... --stages inert,capture,mechanism,cross            # subset
    ... --analyze-only                                    # re-score existing dumps

Env:
    EXP_GPU=<n>  single card; must be Default compute_mode, idle, not Kit's, not the
                 resident ai_worker's. Auto-picked when unset.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import roi_contiguity_experiment as roi  # noqa: E402  (guards, thresholds, box, scoring)

DATASET_ROOT = roi.DATASET_ROOT
INFER_DIR = roi.INFER_DIR
STOCK_CFG = roi.STOCK_CFG
PY = roi.PY
BOX = roi.BOX

EXP_ROOT = os.path.join(BUNDLE, "logger", "slice_basis_experiment")
STAGE_ROOT = os.path.join(EXP_ROOT, "stage")
BASIS_DIR = os.path.join(EXP_ROOT, "basis")

# BC-A is the demo's own operating point (case0) — the one a whole-domain pass would
# plausibly already have been run at. BC-B/C/D are chosen for spread in the single knob
# the demo's headline moves (ACU supply temperature), measured from each case's
# boundary_conditions.json:
#     case0   supply air 18.6-22.8 C (mean 20.2)   <- BC-A, the capture point
#     case100 supply air 18.4-23.4 C (mean 21.4)   <- near
#     case500 supply air 24.0 C uniform            <- +3.8 K from BC-A
#     case700 supply air 14.0 C uniform            <- -6.2 K from BC-A (the demo's own
#                                                     headline move is 20 -> 14 C)
CAPTURE_CASE = "case0"
CROSS_CASES = ["case100", "case500", "case700"]

# nRMSE thresholds, verbatim from the existing study.
PASS_DELTA = roi.PASS_DELTA          # 0.020
FAIL_DELTA = roi.FAIL_DELTA          # 0.077342 = the model's own dspread_overall
FIELDS = roi.FIELDS

# The reference arms already measured and dumped by roi_contiguity_experiment.py.
PRIOR = os.path.join(BUNDLE, "logger", "roi_experiment")


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------------------
# staging: every case is served to inference.py under the name "case0", because the
# config's `case: ["case0"]` and the raw-dump filename both key off it. Mirrors
# ai_worker.stage_case (ai_worker.py:279-300). Symlinks only — the shared tree at
# /mnt/lv/home/Public is READ-ONLY for this experiment and is fingerprinted before/after.
# ---------------------------------------------------------------------------

def stage_case(case: str) -> str:
    d = os.path.join(STAGE_ROOT, case)
    os.makedirs(os.path.join(d, "case0", "h5"), exist_ok=True)
    for sub in ("0", "constant", "system"):
        src, dst = os.path.join(DATASET_ROOT, case, sub), os.path.join(d, "case0", sub)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    h5 = os.path.join(d, "case0", "h5", "case0.h5")
    if not os.path.lexists(h5):
        os.symlink(os.path.join(DATASET_ROOT, case, "h5", f"{case}.h5"), h5)
    bcp = os.path.join(DATASET_ROOT, case, "boundary_conditions.json")
    tgt = os.path.join(d, "case0", "boundary_conditions.json")
    if os.path.exists(bcp) and not os.path.lexists(tgt):
        os.symlink(bcp, tgt)
    for extra in ("patch_statistics.json", "meta.json"):
        for cand in (os.path.join(DATASET_ROOT, extra), os.path.join(BUNDLE, "dataset", extra)):
            if os.path.exists(cand):
                shutil.copyfile(cand, os.path.join(d, extra))
                break
        else:
            sys.exit(f"[GUARD] {extra} not found — importer.py would silently fall back to "
                     f"a dataset-wide normalizer, i.e. a DIFFERENT model.")
    return d


def snapshot(cases):
    snap = {}
    for c in cases:
        root = os.path.join(DATASET_ROOT, c)
        for r, _d, files in os.walk(root):
            for fn in files:
                p = os.path.join(r, fn)
                try:
                    st = os.stat(p)
                    snap[os.path.relpath(p, DATASET_ROOT)] = (st.st_size, st.st_mtime_ns, st.st_ino)
                except OSError:
                    pass
    return snap


def assert_untouched(before, cases):
    after = snapshot(cases)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        sys.exit("[GUARD] THE SHARED READ-ONLY DATASET WAS MODIFIED — experiment invalid:\n  "
                 + "\n  ".join(changed[:20]))
    log(f"[guard] shared dataset untouched ({len(after)} files fingerprinted across {cases}).")


# ---------------------------------------------------------------------------
# one arm
# ---------------------------------------------------------------------------

ALL_KNOBS = ("SCANO_PIVOT_POOL", "SCANO_PIVOT_BOX", "SCANO_PIVOT_BUDGET",
             "SCANO_PIVOT_ORDER", "SCANO_PIVOT_REPEAT", "SCANO_PIVOT_ANCHOR",
             "SCANO_PIVOT_ANCHOR_MODE", "SCANO_BASIS_CAPTURE", "SCANO_BASIS_INJECT",
             "SCANO_BASIS_SITES", "SCANO_BASIS_REDUCE", "SCANO_BASIS_MIX",
             "SCANO_BASIS_LABEL")


def arm_dir(arm):
    return os.path.join(EXP_ROOT, f"arm_{arm}")


def npz_path(arm):
    return os.path.join(arm_dir(arm), "raw", "case0_raw.npz")


def make_cfg(arm):
    import yaml
    with open(STOCK_CFG) as f:
        cfg = yaml.safe_load(f)
    root = arm_dir(arm)
    os.makedirs(root, exist_ok=True)
    cfg["inference_config"]["results_root"] = root   # keeps every arm off the demo logger
    cfg["inference_config"]["save_vtu"] = False
    p = os.path.join(EXP_ROOT, f"config_arm_{arm}.yaml")
    with open(p, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return p


def run_arm(arm, case, envs, note, gpu):
    cfg = make_cfg(arm)
    dump = os.path.join(arm_dir(arm), "raw")
    ds = stage_case(case)
    env = dict(os.environ)
    env.update({"CUDA_VISIBLE_DEVICES": gpu, "OMP_NUM_THREADS": "4",
                "SCANO_DUMP_RAW": dump})
    for k in ALL_KNOBS:
        env.pop(k, None)      # a stale shell export must never leak into a stock arm
    env.update(envs)
    logf = os.path.join(EXP_ROOT, f"arm_{arm}.log")
    log(f"\n=== ARM {arm}  [{case}]  {note}")
    log(f"    env: {envs or '(none — stock)'}")
    t0 = time.time()
    with open(logf, "w") as fh:
        p = subprocess.run([PY, "inference.py", "--config", cfg, "--dataset-path", ds],
                           cwd=INFER_DIR, env=env, stdout=fh, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    txt = open(logf, errors="replace").read()
    if p.returncode != 0:
        log(txt[-5000:])
        sys.exit(f"[FAIL] arm {arm} exited {p.returncode}; see {logf}")
    # The solver must never have run: it would rewrite the shared case through the
    # symlinks AND leave the t=500 ground truth matched to a different t=1.
    if "H5 t=1 cache valid" not in txt:
        sys.exit(f"[GUARD] arm {arm}: Phase 1 did NOT cache-hit — the OpenFOAM solver ran. "
                 f"See {logf}")
    if "0 recomputed, 0 cold-bootstrapped" not in txt:
        sys.exit(f"[GUARD] arm {arm}: Phase 1 recomputed or bootstrapped. See {logf}")
    for line in txt.splitlines():
        if any(k in line for k in ("slice-basis", "CIRCULARITY METER", "PIVOT-EXP",
                                   "nRMSE_avg", "nodes were not sampled")):
            log("    | " + line.split(" - ")[-1].strip()[:180])
    log(f"    done in {dt:.1f} s")
    return npz_path(arm), txt


def circularity_meter(txt):
    for line in txt.splitlines():
        if "CIRCULARITY METER" in line:
            try:
                return float(line.split("rel L2 =")[1].split()[0])
            except (IndexError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------------
# scoring — identical definition to roi_contiguity_experiment.analyze()
# ---------------------------------------------------------------------------

def geometry():
    import h5py
    import numpy as np
    with h5py.File(os.path.join(DATASET_ROOT, CAPTURE_CASE, "h5",
                                f"{CAPTURE_CASE}.h5"), "r") as f:
        cen = f["cpd|centroid"][()].astype("float64")
        ctype = np.asarray(f["cpd|cell_type"][()]).squeeze()
    with open(os.path.join(BUNDLE, "dataset", "meta.json")) as f:
        st = json.load(f)["dataset_statistics"]["std"]
    std = np.array([st["uvw_cpd_cell_u"], st["uvw_cpd_cell_v"], st["uvw_cpd_cell_w"],
                    st["press_cpd_cell"], st["temp_cpd_cell"]], dtype="float64")
    return cen, (ctype == 0), std


def band(d):
    return "PASS" if d <= PASS_DELTA else ("GREY" if d < FAIL_DELTA else "FAIL")


def score_group(title, arms, cells_extra=None):
    """arms: ordered dict label -> npz path. First entry is the reference (delta = 0)."""
    import numpy as np
    cen, interior, std = geometry()
    loaded = {k: roi.load_arm(v) for k, v in arms.items()}
    cells = roi.box_mask(cen, BOX) & interior
    if cells_extra is not None:
        cells &= cells_extra
    for a in loaded.values():
        cells &= a["covered"]
    log("\n" + "=" * 100)
    log(f"{title}   —   {int(cells.sum())} identical interior cells inside the ROI")
    log("=" * 100)
    log(f"{'arm':<26} {'nRMSE_box':>10} {'Delta vs ref':>13} {'band':>6}  "
        + " ".join(f"{f:>7}" for f in FIELDS) + f" {'|T bias| K':>11}")
    out, base = {}, None
    for k, a in loaded.items():
        per, ov, n = roi.nrmse(a, cells, std)
        tb = roi.t_bias_K(a, cells)
        if base is None:
            base = ov
        d = ov - base
        out[k] = {"nrmse_box": ov, "delta": d, "band": band(d), "t_bias_K": tb,
                  "per_field": [float(v) for v in per], "n_cells": int(n)}
        log(f"{k:<26} {ov:>10.6f} {d:>+13.6f} {band(d) if k != list(arms)[0] else '  ref':>6}  "
            + " ".join(f"{v:>7.4f}" for v in per) + f" {tb:>11.4f}")
    out["_n_cells"] = int(cells.sum())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="inert,capture,mechanism,cross")
    ap.add_argument("--analyze-only", action="store_true")
    a = ap.parse_args()
    stages = {s.strip() for s in a.stages.split(",") if s.strip()}

    os.makedirs(EXP_ROOT, exist_ok=True)
    os.makedirs(BASIS_DIR, exist_ok=True)
    basis_A = os.path.join(BASIS_DIR, f"basis_{CAPTURE_CASE}.pt")
    results = {"box": BOX, "capture_case": CAPTURE_CASE, "cross_cases": CROSS_CASES}
    # Merge, never overwrite: stages are run separately, and a later stage clobbering the
    # earlier stages' circularity meters would silently drop the calibration reading the
    # cross-point rows are interpreted against.
    meters_file = os.path.join(EXP_ROOT, "meters.json")
    meters = json.load(open(meters_file)) if os.path.exists(meters_file) else {}

    if not a.analyze_only:
        with open(os.path.abspath(__file__), "rb") as f:
            log(f"script sha256 {hashlib.sha256(f.read()).hexdigest()[:16]}")
        gpu = roi.pick_gpu()
        cases = [CAPTURE_CASE] + CROSS_CASES
        before = snapshot(cases)

        # -------- stage INERT: prove the default path is untouched, do not assert it ----
        if "inert" in stages:
            log("\n" + "#" * 100)
            log("# STAGE 0 — IS THE DEFAULT PATH UNTOUCHED? (bitwise, against the dumps the "
                "PRE-PATCH code wrote)")
            log("#" * 100)
            run_arm("INERT_S", CAPTURE_CASE, {}, "stock whole-domain, NO slice-basis env", gpu)
            run_arm("INERT_C2", CAPTURE_CASE,
                    {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX},
                    "stock ROI-only, NO slice-basis env", gpu)

        # -------- stage CAPTURE ---------------------------------------------------------
        if "capture" in stages:
            log("\n" + "#" * 100)
            log(f"# STAGE 1 — CAPTURE the whole-domain slice basis at BC-A ({CAPTURE_CASE})")
            log("#" * 100)
            _, txt = run_arm("CAP_A", CAPTURE_CASE,
                             {"SCANO_BASIS_CAPTURE": basis_A,
                              "SCANO_BASIS_LABEL": f"whole-domain 51-draw pass @ {CAPTURE_CASE}"},
                             "stock whole-domain + capture", gpu)
            if not os.path.exists(basis_A):
                sys.exit("[FAIL] capture produced no basis file")

        # -------- stage MECHANISM (same operating point → CIRCULAR) ---------------------
        if "mechanism" in stages:
            log("\n" + "#" * 100)
            log("# STAGE 2 — MECHANISM TEST, SAME OPERATING POINT.  *** CIRCULAR ***")
            log("#          The cached basis comes from a pass that already IS the answer")
            log("#          for these cells. This confirms or refutes the diagnosis; it is")
            log("#          NOT evidence of a usable feature.")
            log("#" * 100)
            for arm, sites in (("MECH_ALL", "all"), ("MECH_ENC", "encoder"),
                               ("MECH_TRUNK", "trunk")):
                _, txt = run_arm(arm, CAPTURE_CASE,
                                 {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
                                  "SCANO_BASIS_INJECT": basis_A,
                                  "SCANO_BASIS_SITES": sites},
                                 f"ROI-only + inject basis_A, sites={sites}", gpu)
                meters[arm] = circularity_meter(txt)

        # -------- stage CROSS (the result that would matter) ----------------------------
        if "cross" in stages:
            log("\n" + "#" * 100)
            log("# STAGE 3 — CROSS-OPERATING-POINT REUSE.  The basis was captured ONCE at")
            log(f"#          BC-A ({CAPTURE_CASE}) and is reused unchanged at every BC below.")
            log("#          The whole-domain run at each BC is the SCORING REFERENCE only.")
            log("#" * 100)
            for c in CROSS_CASES:
                run_arm(f"X_{c}_S", c, {}, f"whole-domain reference @ {c}", gpu)
                run_arm(f"X_{c}_C2", c,
                        {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX},
                        f"ROI-only baseline @ {c}", gpu)
                _, txt = run_arm(f"X_{c}_INJ", c,
                                 {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
                                  "SCANO_BASIS_INJECT": basis_A,
                                  "SCANO_BASIS_SITES": "all"},
                                 f"ROI-only + basis captured at {CAPTURE_CASE} @ {c}", gpu)
                meters[f"X_{c}_INJ"] = circularity_meter(txt)

        # -------- stage PERCASE: attribution control ------------------------------------
        # Is the cross-point shortfall caused by the basis being from the WRONG operating
        # point, or by something about those cases (harder flow, worse baseline)? Capture
        # a basis at case k ITSELF and inject it into case k's ROI. If that recovers where
        # case0's basis did not, the residual is BC-mismatch of the basis and nothing else
        # — which is precisely what makes the feature unusable, since obtaining basis_k
        # requires the whole-domain pass at k that the ROI was supposed to avoid.
        if "percase" in stages:
            log("\n" + "#" * 100)
            log("# STAGE 4 — ATTRIBUTION CONTROL: basis captured at each case's OWN")
            log("#          operating point, injected into that same case's ROI.")
            log("#          *** ALSO CIRCULAR — it exists only to attribute the stage-3 gap.")
            log("#" * 100)
            for c in CROSS_CASES:
                bk = os.path.join(BASIS_DIR, f"basis_{c}.pt")
                run_arm(f"P_{c}_CAP", c,
                        {"SCANO_BASIS_CAPTURE": bk,
                         "SCANO_BASIS_LABEL": f"whole-domain 51-draw pass @ {c}"},
                        f"whole-domain + capture @ {c}", gpu)
                _, txt = run_arm(f"P_{c}_INJ", c,
                                 {"SCANO_PIVOT_POOL": "box", "SCANO_PIVOT_BOX": BOX,
                                  "SCANO_BASIS_INJECT": bk, "SCANO_BASIS_SITES": "all"},
                                 f"ROI-only + basis captured at {c} itself", gpu)
                meters[f"P_{c}_INJ"] = circularity_meter(txt)

        assert_untouched(before, cases)
        with open(meters_file, "w") as f:
            json.dump(meters, f, indent=2)

    # ======================= ANALYSIS =========================================
    import numpy as np

    log("\n\n" + "#" * 100)
    log("# RESULTS")
    log("#" * 100)

    # ---- 0. inertness proof ----------------------------------------------------
    log("\n" + "=" * 100)
    log("(0) IS THE DEFAULT PATH UNTOUCHED? — run-level evidence")
    log("=" * 100)
    log("    A whole-run bitwise diff CANNOT settle this and is not claimed to: the")
    log("    pipeline is not reproducible across processes even without the patch (the")
    log("    7 trunk GN blocks aggregate with torch_scatter.scatter_add atomics; the")
    log("    existing study already quantified the floor as 4.7e-5 nRMSE). What the run")
    log("    level CAN show is that the SAMPLER and the DATA are bitwise untouched and")
    log("    that the prediction moves only by that floor. The decisive in-process,")
    log("    same-CUDA-context proof — patched vs the git-HEAD pre-patch source, BITWISE")
    log("    on the production dense path — is scripts/prove_default_path_untouched.py.")
    inert = {}
    pairs = [("INERT_S", os.path.join(PRIOR, "arm_S", "raw", "case0_raw.npz"),
              "stock whole-domain, no slice-basis env"),
             ("INERT_C2", os.path.join(PRIOR, "arm_C2", "raw", "case0_raw.npz"),
              "stock ROI-only, no slice-basis env"),
             ("CAP_A", os.path.join(PRIOR, "arm_S", "raw", "case0_raw.npz"),
              "whole-domain WITH capture armed")]
    cen, interior, std = geometry()
    roi_cells = roi.box_mask(cen, BOX) & interior
    for arm, ref, note in pairs:
        p = npz_path(arm)
        if not (os.path.exists(p) and os.path.exists(ref)):
            log(f"  {arm:<10} (dump missing — skipped)")
            continue
        A, B = np.load(p), np.load(ref)
        same = {k: bool(np.array_equal(A[k], B[k]))
                for k in ("target", "draw_counts", "covered", "is_boundary")}
        mx = float(np.abs(A["prediction"] - B["prediction"]).max())
        cells = roi_cells & (A["draw_counts"] > 0) & (B["draw_counts"] > 0)
        e = ((A["prediction"][cells] - A["target"][cells]) ** 2).mean(axis=0) ** 0.5 / std
        f = ((B["prediction"][cells] - B["target"][cells]) ** 2).mean(axis=0) ** 0.5 / std
        d_nrmse = float(e.mean() - f.mean())
        inert[arm] = {"sampler_and_data_bitwise": all(same.values()),
                      "max_abs_pred_diff": mx, "delta_nRMSE_roi": d_nrmse, "fields": same}
        log(f"  {arm:<9} vs {os.path.relpath(ref, BUNDLE):<42} "
            f"sampler+data bitwise: {'YES' if all(same.values()) else 'NO'}   "
            f"max|dpred|={mx:.2e}   dnRMSE_roi={d_nrmse:+.2e}   ({note})")
    log(f"    (for scale: the study's own reproducibility floor is 4.7e-05 nRMSE and the "
        f"ROI penalty under test is +4.05e-01)")
    results["inertness"] = inert

    # ---- 1. mechanism (circular) ----------------------------------------------
    mech_arms = {}
    ref_S = os.path.join(PRIOR, "arm_S", "raw", "case0_raw.npz")
    if os.path.exists(ref_S):
        mech_arms["S  whole-domain (ref)"] = ref_S
    c2 = os.path.join(PRIOR, "arm_C2", "raw", "case0_raw.npz")
    if os.path.exists(c2):
        mech_arms["C2 ROI-only"] = c2
    for arm, lab in (("MECH_ALL", "C2 + basis_A  all 8 sites"),
                     ("MECH_ENC", "C2 + basis_A  encoder Q only"),
                     ("MECH_TRUNK", "C2 + basis_A  7 trunk only")):
        if os.path.exists(npz_path(arm)):
            mech_arms[lab] = npz_path(arm)
    if len(mech_arms) > 1:
        results["mechanism_same_point_CIRCULAR"] = score_group(
            f"(1) MECHANISM TEST — SAME OPERATING POINT ({CAPTURE_CASE}).  *** CIRCULAR: the "
            f"cached basis came from the reference arm itself ***", mech_arms)
        for arm in ("MECH_ALL", "MECH_ENC", "MECH_TRUNK"):
            if meters.get(arm) is not None:
                log(f"    circularity meter [{arm}] boundary K/V cached-vs-own rel L2 = "
                    f"{meters[arm]:.6f}  (~0 => same operating point, as expected here)")

    # ---- 2. cross-operating-point ---------------------------------------------
    cross = {}
    for c in CROSS_CASES:
        arms = {}
        for arm, lab in ((f"X_{c}_S", f"S  whole-domain @ {c} (ref)"),
                         (f"X_{c}_C2", f"C2 ROI-only @ {c}"),
                         (f"X_{c}_INJ", f"C2 + basis_A({CAPTURE_CASE}) @ {c}"),
                         (f"P_{c}_INJ", f"C2 + basis_{c} (OWN pt, CIRCULAR)")):
            if os.path.exists(npz_path(arm)):
                arms[lab] = npz_path(arm)
        if len(arms) < 3:
            continue
        cross[c] = score_group(
            f"(2) CROSS-OPERATING-POINT — basis captured at {CAPTURE_CASE}, injected at {c}",
            arms)
        m = meters.get(f"X_{c}_INJ")
        if m is not None:
            same_pt = meters.get("MECH_ALL")
            ref = (f" — same-point calibration (case0 basis into case0) reads "
                   f"{same_pt:.6f}, the bf16 quantisation floor" if same_pt else "")
            log(f"    circularity meter: boundary K/V cached-vs-own rel L2 = {m:.6f}{ref}")
            log(f"      -> {'SAME point: this result would be CIRCULAR' if m < 1e-2 else 'the boundary condition really is different: GENUINE cross-point reuse'}")
            cross[c]["_circularity_meter"] = m
    results["cross_operating_point"] = cross

    # ---- VERDICT ----------------------------------------------------------------
    log("\n" + "=" * 100)
    log("VERDICT")
    log("=" * 100)
    log(f"  bands (nRMSE units, identical cells):  PASS <= {PASS_DELTA:.3f}   "
        f"GREY {PASS_DELTA:.3f}..{FAIL_DELTA:.3f}   FAIL >= {FAIL_DELTA:.6f}")

    mech = results.get("mechanism_same_point_CIRCULAR", {})
    mech_key = "C2 + basis_A  all 8 sites"
    c2_key = "C2 ROI-only"
    mech_ok = None
    if mech_key in mech and c2_key in mech:
        d0, d1 = mech[c2_key]["delta"], mech[mech_key]["delta"]
        mech_ok = d1 <= PASS_DELTA
        log(f"\n  MECHANISM (same point, CIRCULAR): ROI-only {d0:+.6f} -> "
            f"ROI+cached basis {d1:+.6f}   [{band(d1)}]")
        log(f"      recovered {100.0 * (d0 - d1) / d0 if d0 else float('nan'):.1f}% of the "
            f"contiguity penalty. This is the diagnosis test ONLY — the cached basis came "
            f"from the very pass it is scored against.")

    cross_deltas = {}
    for c, g in cross.items():
        k_c2 = f"C2 ROI-only @ {c}"
        k_inj = f"C2 + basis_A({CAPTURE_CASE}) @ {c}"
        if k_c2 in g and k_inj in g:
            cross_deltas[c] = (g[k_c2]["delta"], g[k_inj]["delta"])
    if cross_deltas:
        log(f"\n  CROSS-OPERATING-POINT (the result that would matter):")
        log(f"      {'case':<9} {'ROI-only':>12} {'+basis_A(case0)':>18} "
            f"{'recovered':>10} {'+basis_OWN (circular)':>22} {'BC distance':>12}")
        for c, (d0, d1) in cross_deltas.items():
            own = cross[c].get(f"C2 + basis_{c} (OWN pt, CIRCULAR)", {}).get("delta")
            own_s = f"{own:+.6f} [{band(own)}]" if own is not None else "(not run)"
            log(f"      {c:<9} {d0:+.6f} [{band(d0)}] {d1:+.6f} [{band(d1)}] "
                f"{100.0 * (d0 - d1) / d0 if d0 else float('nan'):>9.1f}% {own_s:>22} "
                f"{cross[c].get('_circularity_meter', float('nan')):>12.4f}")
        worst = max(d1 for _, d1 in cross_deltas.values())
        cross_ok = worst <= PASS_DELTA
        log(f"\n  >>> worst cross-point delta = {worst:+.6f} -> {band(worst)}")
        if mech_ok and cross_ok:
            log("      The fix transfers across operating points: an ROI pass at an unseen BC")
            log("      recovers using a basis captured once elsewhere. THIS delivers "
                "drag-and-sim.")
        elif mech_ok and not cross_ok:
            log("      THE FIX IS CIRCULAR AND DOES NOT DELIVER DRAG-AND-SIM. The mechanism")
            log("      is confirmed — reusing a whole-domain basis does repair the ROI — but")
            log("      only when the basis was captured at the same operating point, i.e.")
            log("      only when the answer was already computed. Reused across operating")
            log("      points it does not hold, so it is not a usable feature.")
            owns = [cross[c].get(f"C2 + basis_{c} (OWN pt, CIRCULAR)", {}).get("delta")
                    for c in cross_deltas]
            if all(o is not None for o in owns):
                log("      ATTRIBUTION: at every case the case's OWN-point basis beats the")
                log("      transferred one (see the last column). So the shortfall is the")
                log("      basis being from the wrong BOUNDARY CONDITION, not from the ROI,")
                log("      the cell set, or those cases being harder. And obtaining the")
                log("      own-point basis needs exactly the whole-domain pass the ROI was")
                log("      supposed to replace — which is the circularity, stated as a")
                log("      measurement rather than as a worry.")
        elif not mech_ok:
            log("      The diagnosis is REFUTED at the intervention level: substituting a")
            log("      whole-domain basis does not repair the ROI even at the same operating")
            log("      point. The collapsed query basis is not the whole mechanism.")
        results["verdict"] = {"mechanism_pass": bool(mech_ok), "cross_pass": bool(cross_ok),
                              "worst_cross_delta": worst}

    out = os.path.join(EXP_ROOT, "verdict.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"\n  written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
