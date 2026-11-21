#!/usr/bin/env python3
"""SCANO_PIVOT_BOX must arrive in the SOLVER frame, whatever frame the caller speaks.

THE BUG THIS PINS. The demo's ROI box is authored in the demo world frame (Y-up). The
EXPORT path converts it once, in the inferencer, using the full mesh's depth
(`inferencer.py:232` -> `export_roi.resolve_box`). The PIVOT path did not convert at all —
`ai_worker.run_inference` stuffed the demo-frame numbers straight into SCANO_PIVOT_BOX,
which `_init_sampling` compares against `cpd|centroid`, i.e. solver-frame coordinates.

The two boxes therefore addressed DIFFERENT regions of the room, and nothing failed loudly:
the 45,000 room-wide anchors dominate the pool, so the run still produced a plausible field.
Proof from the shipped logs — a live sim-ROI run reported `pool 53389 ids`, which matches
9,360 box ids (the demo numbers read as solver) unioned with 45,000 anchors, not the 14,648
the exporter selected for the same drag.

Backwards compatibility is the other half. Every arm of the K sweep and the slice-basis
study passed a bare 6-number CSV meaning solver coordinates. That form must keep meaning
exactly what it meant, or 93 recorded runs silently change meaning.

    /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python scripts/test_pivot_box_frame.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
A3 = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(A3, "src"))
sys.path.insert(0, A3)

H5 = "/mnt/lv/home/Public/GTC/processed_dataset_unconverged/case0/h5/case0.h5"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def env(**kw):
    for k in list(os.environ):
        if k.startswith("SCANO_PIVOT"):
            del os.environ[k]
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = str(v)


def main():
    import json

    import numpy as np

    from FVdomain.HomoGraph_dataset import _parse_pivot_roi, _pivot_box_to_solver

    print("[A] the bare CSV form keeps meaning SOLVER coordinates (93 recorded arms)")
    env(SCANO_PIVOT_POOL="box", SCANO_PIVOT_BOX="13.489,7.514,0,17.511,11.537,5")
    r = _parse_pivot_roi()
    check("bare CSV parses", r["box"] == [13.489, 7.514, 0.0, 17.511, 11.537, 5.0])
    check("bare CSV is solver frame", r.get("box_frame") == "solver",
          f"got {r.get('box_frame')!r}")
    lo, hi = _pivot_box_to_solver(r, depth=19.0)
    check("bare CSV is passed through untouched",
          list(lo) == [13.489, 7.514, 0.0] and list(hi) == [17.511, 11.537, 5.0],
          f"{lo} {hi}")

    print("[B] the JSON form carries a frame")
    env(SCANO_PIVOT_POOL="box+anchor",
        SCANO_PIVOT_BOX=json.dumps({"min": [8, 0.5, 2], "max": [16, 3, 7],
                                    "frame": "demo"}))
    r = _parse_pivot_roi()
    check("JSON demo frame parses", r.get("box_frame") == "demo", f"{r.get('box_frame')!r}")
    lo, hi = _pivot_box_to_solver(r, depth=19.0)
    # export_roi.demo_box_to_solver: solver = (x, depth - z_demo, y_demo), depth axis FLIPS.
    check("demo -> solver conversion matches the exporter's receipt",
          [round(v, 6) for v in lo] == [8.0, 12.0, 0.5]
          and [round(v, 6) for v in hi] == [16.0, 17.0, 3.0],
          f"{lo}-{hi}, expected (8,12,0.5)-(16,17,3)")

    env(SCANO_PIVOT_POOL="box",
        SCANO_PIVOT_BOX=json.dumps({"min": [8, 12, 0.5], "max": [16, 17, 3],
                                    "frame": "solver"}))
    r = _parse_pivot_roi()
    lo, hi = _pivot_box_to_solver(r, depth=19.0)
    check("JSON solver frame is passed through untouched",
          [round(v, 6) for v in lo] == [8.0, 12.0, 0.5]
          and [round(v, 6) for v in hi] == [16.0, 17.0, 3.0], f"{lo}-{hi}")

    env(SCANO_PIVOT_POOL="box",
        SCANO_PIVOT_BOX=json.dumps({"min": [1, 1, 1], "max": [2, 2, 2],
                                    "frame": "world"}))
    try:
        _parse_pivot_roi()
        check("an unknown frame is refused", False, "no exception")
    except ValueError as e:
        check("an unknown frame is refused", "frame" in str(e).lower(), str(e)[:70])

    print("[C] THE REGRESSION, against the real mesh: the pivot box must select the same "
          "cells the exporter selects")
    if not os.path.exists(H5):
        check("real-mesh check", False, f"dataset missing: {H5}")
    else:
        import h5py
        with h5py.File(H5, "r") as f:
            cen = f["cpd|centroid"][()].astype("float64")
            depth = float(f["node|pos"][()][:, 1].max())
        check("depth read from node|pos matches the exporter's receipt",
              abs(depth - 19.0) < 1e-6, f"depth={depth}")

        def sel(lo, hi):
            lo = np.asarray(lo, dtype=np.float64)
            hi = np.asarray(hi, dtype=np.float64)
            return int(((cen >= lo) & (cen <= hi)).all(axis=1).sum())

        n_export = sel([8.0, 12.0, 0.5], [16.0, 17.0, 3.0])     # what the exporter cut
        n_raw = sel([8.0, 0.5, 2.0], [16.0, 3.0, 7.0])          # the un-converted demo box
        check("the un-converted demo box selects a DIFFERENT set (the bug)",
              n_raw != n_export, f"raw={n_raw} export={n_export}")

        env(SCANO_PIVOT_POOL="box+anchor",
            SCANO_PIVOT_BOX=json.dumps({"min": [8, 0.5, 2], "max": [16, 3, 7],
                                        "frame": "demo"}))
        lo, hi = _pivot_box_to_solver(_parse_pivot_roi(), depth=depth)
        check("the converted demo box selects EXACTLY the exported cells",
              sel(lo, hi) == n_export, f"got {sel(lo, hi)}, exporter got {n_export}")

    env()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
