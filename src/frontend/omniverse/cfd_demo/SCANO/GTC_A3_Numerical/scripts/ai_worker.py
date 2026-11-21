#!/usr/bin/env python3
"""Resident AI-inference worker for the Omniverse CFD demo.

Why a separate long-lived process
---------------------------------
Kit's main thread drives the RTX render and the WebRTC stream. Running a PyTorch forward
pass inside it would freeze the viewport for the whole run and can drop the single
streaming client. So inference lives out of process and the UI talks to it over HTTP.

What is actually kept warm (and what is not)
--------------------------------------------
* WARM: this server process only.
* NOT WARM: the inference itself, which is launched as a subprocess so we do not have to
  refactor the monolithic ``inference.py``. That costs ~13 s of compute plus ~10 s of
  torch import per request. Making it truly resident means hoisting Runner/Inferencer
  construction out of ``main()``.

The demo serves the NUMERICAL (A3) arm, so there is no text encoder to keep warm: the
boundary reaches the model as the per-face t=1 field, z-scored per patch. That costs one
relaxed OpenFOAM step per request and is what makes the sliders matter at all — the
frozen-text arm returned the same field for every operating point.

Measured single-GPU, warm: 1-step solve ~13 s + inference ~13 s + adapt ~10 s, i.e. a
~45 s click. The first request after a restart adds ~35 s of model/CUDA load.

GPU policy
----------
The GPU list is explicit and never auto-selected. The shipped ``run_inference.sh``
picks idle GPUs sorted DESCENDING (`sort -rn`), i.e. it takes card 7 first — which is
the card the Omniverse renderer runs on (`start_demo.sh` DC_CFD_GPU default 7). An
archived sweep log literally reads ``Selected GPUs: 7,6,5,4,3,2``. We therefore bypass
that launcher entirely and set CUDA_VISIBLE_DEVICES ourselves.

More than one GPU switches on the paper's TTA path: the pivot pool is shuffled with a
rank-independent seed, split into contiguous per-rank chunks, and each rank scores only its
own chunk; the full-size prediction and count buffers are then summed with
``dist.all_reduce`` over NCCL before the count-normalised divide, which is the overlap
averaging.

Do NOT describe this as a pure speed knob — an earlier version of this docstring did, and
it was wrong. The paper states that "the reported accuracy therefore depends on the rank
count" (appendix.tex:609) and quantifies it: at rho=1.0 on the layout set the single-stream
control goes 0.3488 (R=1) to 0.1948 (R=6). The accuracy comes from the NUMBER of
accumulated overlapping passes rather than from the rank count itself — the paper tests
that directly and finds 1 rank x 20 passes (0.6018) indistinguishable from 6 ranks x 4
passes (0.5986). Ranks are simply how those passes are collected in parallel.

Measured on this bundle (case0, three runs each): 1 GPU 14.29 s forward vs 2 GPUs 8.45 s,
a 1.69x speed-up, with nRMSE matching to five decimals (0.192502 vs 0.192518). At two ranks
the accuracy is already saturated for this case, so the win there is purely wall time.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BUNDLE, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = int(os.environ.get("DC_CFD_AI_PORT", "8302"))
GPUS = [g.strip() for g in os.environ.get("DC_CFD_AI_GPUS", "0").split(",") if g.strip()]
PY = sys.executable
CFG = os.path.join(BUNDLE, "cases", "GTC", "A3", "configs", "config_A3_inference.yaml")
INFER_DIR = os.path.join(BUNDLE, "cases", "GTC", "A3")
DATASET_ROOT = os.environ.get(
    "DC_CFD_AI_DATASET", "/mnt/lv/home/Public/GTC/processed_dataset_unconverged")
RESULTS = os.path.join(BUNDLE, "logger", "inference_results")
WORK = os.environ.get("DC_CFD_AI_WORK", "/tmp/dc_cfd_ai_work")
VTU_OUT = os.path.join(BUNDLE, "logger", "vtu_output", "case0", "case0.vtu")
# Follows DATASET_ROOT instead of repeating the literal. It used to be its own
# absolute constant, so setting DC_CFD_AI_DATASET moved the dataset but left this
# pointing at the old machine — every slider prediction then failed inside BC
# synthesis, while the status endpoint still reported the worker healthy.
BASE_CASE = os.environ.get("DC_CFD_AI_BASE_CASE", os.path.join(DATASET_ROOT, "case0"))

_lock = threading.Lock()
_state = {"busy": False, "last": None, "runs": 0}


def log(msg: str):
    """One line per event. start_demo.sh redirects this to a file the UI tails, so the
    prefix is the contract the frontend matches on."""
    print(f"[ai_worker] {msg}", flush=True)


def _free_port() -> int:
    """A port nobody is holding right now.

    torch.distributed.run needs a rendezvous port. A FIXED one (we used 29571) fails
    intermittently: after a run ends the port sits in TIME_WAIT, and a second prediction
    a minute later dies with ChildFailedError before any of our code runs. That looked
    like a model failure in the UI but was purely a socket collision.
    """
    import socket
    with socket.socket() as sk:
        sk.bind(("", 0))
        return int(sk.getsockname()[1])


def _roi_receipt_path(vtu_path: str) -> str:
    return os.path.splitext(vtu_path)[0] + ".scano_roi.json"


def normalise_roi(roi) -> dict | None:
    """Validate the UI's ROI and put it in the shape the exporter reads.

    The box arrives in the DEMO world frame — metres, Y-up, the same frame the ROI box in
    CfdControls.tsx uses. It is NOT converted here: the demo-frame -> solver-frame
    translation needs the full domain's depth, which only the exporter knows for certain,
    and re-deriving it in a second place is precisely how a subset ends up rendered at the
    wrong depth. So the frame is stamped on the box and the conversion happens once.
    """
    if not roi:
        return None
    if not isinstance(roi, dict):
        raise ValueError(f"roi must be an object with min/max, got {type(roi).__name__}")
    lo, hi = roi.get("min"), roi.get("max")
    if not (isinstance(lo, (list, tuple)) and isinstance(hi, (list, tuple))
            and len(lo) == 3 and len(hi) == 3):
        raise ValueError(f'roi needs "min" and "max" as 3-element arrays, got {roi!r}')
    lo = [float(v) for v in lo]
    hi = [float(v) for v in hi]
    lo, hi = [min(a, b) for a, b in zip(lo, hi)], [max(a, b) for a, b in zip(lo, hi)]
    if not all(hi[i] > lo[i] for i in range(3)):
        raise ValueError(f"roi has zero extent on some axis: min={lo} max={hi}")
    return {"min": lo, "max": hi, "frame": "demo"}


# Requirement 1's "only simulate the ROI", as far as it is achievable WITHOUT retraining.
#
# A pivot pool restricted to the box fails outright (+0.406 nRMSE, 8944-arm measurements
# in 8da9599/04f2f57). What passes is the box UNIONED with a room-wide sprinkle of K
# anchors, because the failure is the pivot cloud's spatial EXTENT collapsing the query
# basis, not the ROI itself. Measured on the identical 9,636 ROI cells:
#
#     K=0      +0.4060 FAIL    3.1% of the domain touched
#     K=30000  +0.0054 PASS   42.6%
#     K=45000  -0.0029 PASS   54.7%   <- better than the 51-draw baseline, at 7.18% of
#                                        its node-forwards
#     K=60000  +0.0215 GREY   63.5%   <- K is NOT monotone; do not extrapolate upward
#
# Default 45000 = the optimum measured ON THAT BOX. Read the caveat before quoting it:
# every scored arm above used the study box 13.489,7.514,0-17.511,11.537,5 (4x4x5 m,
# 9,636 interior cells). NO arm has ever been scored at a box a demo user drags, so
# -0.0029 is not established for an arbitrary ROI — only the mechanism is. Treat 45000 as
# a defensible default, not a measured guarantee for your box.
#
# And note honestly what this is and is not: it does NOT touch only the ROI (it touches
# 54.7% of the domain, of which the pivots are 12.5% and the rest is their k-hop context).
# What it saves is COMPUTE, because the stock path spends 51 redundant scattered draws.
# Calling it "simulate only the ROI" would be false; it is one distribution-matched draw
# replacing 51.
SIM_ROI_ANCHORS = int(os.environ.get("DC_CFD_SIM_ROI_ANCHORS", "45000"))


def run_inference(work_dir: str, attempt: int = 0, roi: dict | None = None,
                  sim_roi: bool = False) -> str:
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ",".join(GPUS)
    env.setdefault("OMP_NUM_THREADS", "4")
    # Restricts only what the exporter WRITES. The forward pass still covers the whole
    # domain — a box-restricted pivot pool was measured and fails (8da9599: a 3% ROI costs
    # +0.406 nRMSE against a 0.077 threshold, T error 1.03 K vs the 0.94 K signal the demo
    # is built to show). Absent roi: the variable is not set and nothing changes.
    if roi:
        env["SCANO_EXPORT_ROI"] = json.dumps(roi)
        log(f"ROI export: min={roi['min']} max={roi['max']} (demo frame) — "
            f"inference still runs over the whole domain")
    else:
        env.pop("SCANO_EXPORT_ROI", None)
    # Opt-in: also RESTRICT the pivots to the ROI plus K room-wide anchors, instead of
    # only restricting the export. Off by default, because it trades exactness for speed
    # and the caller should choose that knowingly.
    if roi and sim_roi:
        # Hand over the SAME frame-stamped object the exporter gets, not bare numbers.
        # cpd|centroid is solver-frame; a bare CSV means solver, so passing the demo
        # numbers that way put the pivot cloud over a DIFFERENT region than the one being
        # exported (9,360 cpd ids instead of 14,648) and nothing failed loudly, because
        # the anchors dominate the pool. The conversion now happens once, next to the
        # centroids, using the full mesh's depth.
        env["SCANO_PIVOT_POOL"] = "box+anchor"
        env["SCANO_PIVOT_BOX"] = json.dumps(roi)
        env["SCANO_PIVOT_ANCHOR"] = str(SIM_ROI_ANCHORS)
        log(f"SIM-ROI: pivots = box {roi['min']}-{roi['max']} ({roi['frame']} frame) + "
            f"{SIM_ROI_ANCHORS} room-wide anchors. K was sized on a DIFFERENT box "
            f"(see SIM_ROI_ANCHORS); this touches ~55% of the domain, it does not "
            f"touch only the ROI")
    else:
        for _k in ("SCANO_PIVOT_POOL", "SCANO_PIVOT_BOX", "SCANO_PIVOT_ANCHOR"):
            env.pop(_k, None)
    n = len(GPUS)
    if n > 1:
        cmd = [PY, "-m", "torch.distributed.run",
               f"--nproc_per_node={n}", f"--master_port={_free_port()}",
               "inference.py"]
        log(f"launching multi-GPU TTA inference on cards {','.join(GPUS)} ({n} ranks)")
    else:
        cmd = [PY, "inference.py"]
        log(f"launching single-GPU inference on card {GPUS[0]}")
    cmd += ["--config", CFG, "--dataset-path", work_dir]

    if os.path.exists(VTU_OUT):
        os.remove(VTU_OUT)
    # The receipt must never outlive the VTU it describes: left behind, a previous ROI
    # run's depth would be applied to this run's whole-domain field.
    if os.path.exists(_roi_receipt_path(VTU_OUT)):
        os.remove(_roi_receipt_path(VTU_OUT))
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=INFER_DIR, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    dt = time.monotonic() - t0
    if p.returncode != 0 or not os.path.exists(VTU_OUT):
        log(f"FAILED inference rc={p.returncode} after {dt:.1f}s")
        # Surface the REAL error, not just torch.distributed's wrapper: keep the lines
        # that name an exception so a rendezvous collision is distinguishable from an
        # actual model/data fault.
        lines = (p.stdout or "").splitlines()
        interesting = [l for l in lines
                       if any(k in l for k in ("Error", "error", "Exception", "Traceback",
                                               "CUDA", "assert", "FAILED", "ROI"))][-12:]
        for l in interesting:
            log("  " + l.strip()[:160])
        # A DETERMINISTIC input error must not be retried, and must not be reported as a
        # port collision. An ROI that selects no cells is reachable straight from the UI —
        # the box inputs are not clamped to the room, and a box dragged entirely inside a
        # rack contains no fluid cells either. Retrying it cost two full forward passes
        # (measured 45.7 s) and then told the user "inference failed rc=0", which names
        # the wrong cause and hides the one thing they could act on.
        _empty = [l for l in lines if "ROI selects 0 of" in l]
        if _empty:
            raise RuntimeError(
                "the ROI contains no cells — move or enlarge the box "
                f"({_empty[-1].strip()[:160]})")
        if attempt == 0:
            # One retry on a fresh rendezvous port. Transient collisions are the common
            # case; a genuine fault fails the same way twice and we stop.
            log("retrying once on a fresh port")
            time.sleep(2)
            return run_inference(work_dir, attempt=1, roi=roi, sim_roi=sim_roi)
        raise RuntimeError(f"inference failed rc={p.returncode}")
    for line in (p.stdout or "").splitlines():
        if "nRMSE_avg" in line or "Inference time" in line:
            log(line.strip()[-120:])
        # The exporter's own receipt: cells written / cells available, both boxes, the
        # path. Re-emitted verbatim so the count the UI shows is the count the writer
        # wrote, not one re-derived from the adapted file.
        if "SCANO_ROI_EXPORT" in line:
            log(line[line.index("SCANO_ROI_EXPORT"):].strip())
    log(f"inference done in {dt:.1f}s")
    return VTU_OUT


def stage_bc(bc: dict) -> str:
    """Materialise a case from UI-supplied boundary conditions and let inference run the
    1-step solver.

    This is the full chain, now that the solver reproduces the dataset's t=1 exactly
    (verified: T/U/p_rgh all match to 0.0000% relative L2). It was broken until the A3
    tree got its own fvSolution.jinja2 — without it the template search fell through to a
    legacy "every tolerance > 1" minimal file, the step never converged (T stayed at the
    uniform 297.15 initial value, U blew up to 366 m/s), and the model was handed a
    diverged boundary field. The failure was silent: the log still said "t=1 computed".
    """
    from bc_synth import apply_bc
    work = os.path.join(WORK, "bc_case")
    shutil.rmtree(work, ignore_errors=True)
    case_dir = os.path.join(work, "case0")
    apply_bc(BASE_CASE, case_dir, bc)
    os.makedirs(os.path.join(case_dir, "h5"), exist_ok=True)
    shutil.copyfile(os.path.join(BASE_CASE, "h5", "case0.h5"),
                    os.path.join(case_dir, "h5", "case0.h5"))
    for extra in ("patch_statistics.json", "meta.json"):
        for cand in (os.path.join(DATASET_ROOT, extra),
                     os.path.join(BUNDLE, "dataset", extra)):
            if os.path.exists(cand):
                shutil.copyfile(cand, os.path.join(work, extra))
                break
    return work


def stage_case(case: str) -> str:
    """Serve an EXISTING dataset case: its t=1 is already there, so no solver runs."""
    d = os.path.join(WORK, f"ds_{case}")
    os.makedirs(os.path.join(d, "case0", "h5"), exist_ok=True)
    for sub in ("0", "constant", "system"):
        src = os.path.join(DATASET_ROOT, case, sub)
        dst = os.path.join(d, "case0", sub)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    h5 = os.path.join(d, "case0", "h5", "case0.h5")
    if not os.path.lexists(h5):
        os.symlink(os.path.join(DATASET_ROOT, case, "h5", f"{case}.h5"), h5)
    for extra in ("patch_statistics.json", "meta.json"):
        for cand in (os.path.join(DATASET_ROOT, extra),
                     os.path.join(BUNDLE, "dataset", extra)):
            if os.path.exists(cand):
                shutil.copyfile(cand, os.path.join(d, extra))
                break
    bcp = os.path.join(DATASET_ROOT, case, "boundary_conditions.json")
    tgt = os.path.join(d, "case0", "boundary_conditions.json")
    if os.path.exists(bcp) and not os.path.lexists(tgt):
        os.symlink(bcp, tgt)
    return d


def handle_predict(payload: dict) -> dict:
    from vtu_to_demo import adapt

    roi = normalise_roi(payload.get("roi"))
    # simRoi additionally restricts the PIVOTS (see SIM_ROI_ANCHORS). Meaningless without
    # a box, so it is ignored rather than half-applied when roi is absent.
    sim_roi = bool(payload.get("simRoi")) and roi is not None
    bc = payload.get("bc")
    # A bc dict means the user moved the sliders: synthesise the operating point and run
    # the solver (~45-75 s). A bare case name replays a dataset operating point (~30 s).
    #
    # Check EVERY boundary key, not just server_powers. The UI sends only the entries the
    # user actually moved, so an ACU-only change arrives as {"supply_air_temperatures": ...}
    # with no server_powers at all. Gating on server_powers alone sent that straight down
    # the dataset-replay path: the demo's single most visible knob — dropping CRAC supply
    # from 20 to 14 C, worth -0.94 K over 84.7% of points — silently rendered the stock
    # case and looked like the model ignoring the user.
    if bc and any(bc.get(k) for k in ("server_powers", "server_volume_flow_rates",
                                      "supply_air_temperatures",
                                      "supply_air_volume_flow_rates")):
        label = "sliders"
        log("synthesising the operating point from the UI (will run a 1-step solve)")
        work = stage_bc(bc)
    else:
        label = str(payload.get("case") or "case0")
        log(f"serving dataset case {label} (t=1 already present — no solver)")
        work = stage_case(label)

    os.makedirs(RESULTS, exist_ok=True)
    t0 = time.monotonic()
    src = run_inference(work, roi=roi, sim_roi=sim_roi)

    stamp = time.strftime("%Y-%m-%d-%H-%M-%S")
    dst = os.path.join(RESULTS, f"prediction_{label}_{stamp}.vti")
    log("adapting to the demo frame (Z-up -> Y-up, T/U/Umag)")
    # adapt() picks the full-domain depth out of the receipt beside `src` on its own; it
    # is not passed here so that the CLI and the worker resolve it the same single way.
    info = adapt(src, dst)
    with open(os.path.join(RESULTS, f"prediction_{label}_{stamp}.ranges.json"), "w") as f:
        st = info["stats"]
        json.dump({"T": [st["T"]["p1"], st["T"]["p99"]],
                   "U": [st["Umag"]["p1"], st["Umag"]["p99"]], "_actual": st}, f, indent=2)
    latest = os.path.join(RESULTS, "latest.vti")
    shutil.copyfile(dst, latest)

    # Carry the export receipt into the HTTP response and keep a copy beside the result,
    # so "you are looking at 8,123 of 344,629 cells" is answerable without the log.
    receipt = None
    rp = _roi_receipt_path(src)
    if os.path.exists(rp):
        with open(rp) as f:
            receipt = json.load(f)
        receipt["vti"] = dst
        shutil.copyfile(rp, os.path.join(RESULTS, f"prediction_{label}_{stamp}.roi.json"))

    total = time.monotonic() - t0
    log(f"OK {dst} ({total:.1f}s total)")
    out = {"ok": True, "mode": label, "vtu": dst, "latest": latest,
           "seconds": round(total, 1), "stats": info["stats"], "n_cells": info["n_cells"],
           "depth_source": info.get("depth_source")}
    if receipt:
        out["roi"] = receipt
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):     # silence the default stderr access log
        pass

    def do_GET(self):
        if self.path.startswith("/status"):
            self._send(200, {"ok": True, "gpus": GPUS, "tta": len(GPUS) > 1,
                             "busy": _state["busy"], "runs": _state["runs"],
                             "last": _state["last"], "results_dir": RESULTS})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/predict"):
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._send(400, {"error": f"bad json: {e}"})
            return
        if not _lock.acquire(blocking=False):
            self._send(409, {"error": "a prediction is already running"})
            return
        try:
            _state["busy"] = True
            res = handle_predict(payload)
            _state["runs"] += 1
            _state["last"] = res
            self._send(200, res)
        except Exception as e:
            log(f"ERROR {e}")
            log(traceback.format_exc()[-1500:])
            self._send(500, {"ok": False, "error": str(e)})
        finally:
            _state["busy"] = False
            _lock.release()


def _uses_text_conditioning() -> bool:
    """True when the bundle's config asks for the frozen-text boundary encoder."""
    try:
        import yaml
        with open(CFG) as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return True  # unknown config: warm it, a slow start beats a slow first click
    hit = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("use_text_conditioning", "patch_only_numerical"):
                    hit.append((k, v))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cfg)
    if any(k == "patch_only_numerical" and v for k, v in hit):
        return False
    return any(k == "use_text_conditioning" and v for k, v in hit)


def _scrub_foreign_pythonpath() -> None:
    """Drop PYTHONPATH entries built for a different Python than the one running us.

    start_demo.sh exports a python3.10 PYTHONPATH for Kit's vtk delegate. This worker is a
    python3.11 interpreter, and inheriting that path made it import the system numpy 1.21.5
    instead of its own: every inference failed with "No module named
    numpy.core._multiarray_umath". The launcher now unsets it, but the worker is also
    started by hand, so refuse the bad path here too — and do it before the first import
    that would trip over it. Whatever is left is passed on to the inference subprocess.
    """
    raw = os.environ.get("PYTHONPATH")
    if not raw:
        return
    mine = f"python{sys.version_info.major}.{sys.version_info.minor}"
    other = re.compile(r"python3\.\d+")
    kept, dropped = [], []
    for part in raw.split(os.pathsep):
        m = other.search(part)
        if (m and m.group(0) != mine) or "/usr/lib/python3/dist-packages" in part:
            dropped.append(part)
        elif part:
            kept.append(part)
    if not dropped:
        return
    if kept:
        os.environ["PYTHONPATH"] = os.pathsep.join(kept)
    else:
        os.environ.pop("PYTHONPATH", None)
    # os.environ alone is not enough: the interpreter copied PYTHONPATH into sys.path at
    # startup, so the bad directories are already ahead of the env's own site-packages and
    # the next `import numpy` still resolves there. Clearing the environment variable only
    # fixes CHILD processes. Drop them from sys.path as well, before anything imports.
    bad = {os.path.normpath(p) for p in dropped}
    sys.path[:] = [p for p in sys.path if os.path.normpath(p) not in bad]
    log(f"dropped {len(dropped)} foreign PYTHONPATH entr"
        f"{'y' if len(dropped) == 1 else 'ies'} (not {mine}): {', '.join(dropped)}")


def main():
    _scrub_foreign_pythonpath()
    os.makedirs(RESULTS, exist_ok=True)
    log(f"bundle={BUNDLE}")
    log(f"gpus={GPUS} tta={'on' if len(GPUS) > 1 else 'off'} port={PORT}")
    # Warm the text encoder ONLY for a text-conditioned bundle. A3 encodes the boundary
    # numerically (patch_only_numerical) and never loads MiniLM, so warming it there costs
    # ~58 s of startup for a model that is then never called.
    if _uses_text_conditioning():
        try:
            t0 = time.monotonic()
            from transformers import AutoModel, AutoTokenizer
            AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
            AutoModel.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
            log(f"MiniLM warmed in {time.monotonic()-t0:.1f}s")
        except Exception as e:
            log(f"MiniLM warmup skipped ({e})")
    else:
        log("numerical boundary encoding — no text encoder to warm")
    log(f"READY http://0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
