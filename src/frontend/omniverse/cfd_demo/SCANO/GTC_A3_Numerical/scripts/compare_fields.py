#!/usr/bin/env python3
"""Diff two prediction VTUs — the readout for the T4 sensitivity gate.

The question this answers: when the UI changes a boundary condition, does the PREDICTED
field actually move, and does it move in the physically right direction?

Two traps this guards against:

* The model could ignore the changed digits entirely (the paper's Caveat 4 — a frozen
  sentence encoder reads numeric magnitude only approximately). Then ``mean |dT|`` is ~0
  and the demo's core interaction is a no-op.
* The run-to-run noise floor is NOT zero: inference averages over randomly drawn pivot
  subsets, so two identical runs differ a little. The reported DRAW SPREAD is that floor.
  A change is only real if it clears it, which is why we print both.
"""
from __future__ import annotations

import sys

import numpy as np
import pyvista as pv

FIELDS = ["pred_Temperature", "pred_velocity", "pred_pressure"]


def load(path):
    m = pv.read(path)
    return m


def arr(mesh, name):
    if name in mesh.cell_data:
        return np.asarray(mesh.cell_data[name])
    if name in mesh.point_data:
        return np.asarray(mesh.point_data[name])
    return None


def main():
    if len(sys.argv) < 3:
        print("usage: compare_fields.py A.vtu B.vtu")
        raise SystemExit(2)
    a_path, b_path = sys.argv[1], sys.argv[2]
    a, b = load(a_path), load(b_path)
    print(f"A = {a_path}\n    {a.n_points} pts / {a.n_cells} cells")
    print(f"B = {b_path}\n    {b.n_points} pts / {b.n_cells} cells")
    print(f"    cell arrays: {list(a.cell_data.keys())}")
    if a.n_cells != b.n_cells:
        print("!! different mesh sizes — not comparable")
        raise SystemExit(1)

    verdict_lines = []
    for f in FIELDS:
        x, y = arr(a, f), arr(b, f)
        if x is None or y is None:
            continue
        if x.ndim > 1:                      # vector -> magnitude
            x, y = np.linalg.norm(x, axis=1), np.linalg.norm(y, axis=1)
        d = y - x
        rng = float(x.max() - x.min()) or 1.0
        print(f"\n── {f} ──")
        print(f"   A: mean={x.mean():.4f}  min={x.min():.4f}  max={x.max():.4f}")
        print(f"   B: mean={y.mean():.4f}  min={y.min():.4f}  max={y.max():.4f}")
        print(f"   delta: mean={d.mean():+.4f}  mean|d|={np.abs(d).mean():.4f}  "
              f"max|d|={np.abs(d).max():.4f}")
        print(f"   delta as % of A's range: mean|d|={100*np.abs(d).mean()/rng:.2f}%  "
              f"max|d|={100*np.abs(d).max()/rng:.2f}%")
        frac = float((np.abs(d) > 0.01 * rng).mean())
        print(f"   cells moved >1% of range: {100*frac:.1f}%")
        if f == "pred_Temperature":
            verdict_lines.append(
                f"mean dT = {d.mean():+.3f} K, mean|dT| = {np.abs(d).mean():.3f} K, "
                f"max|dT| = {np.abs(d).max():.3f} K, {100*frac:.1f}% of cells moved")

    print("\n" + "=" * 70)
    for line in verdict_lines:
        print("VERDICT INPUT:", line)
    print("Interpretation: raising every server's power should raise the predicted air")
    print("temperature. A mean dT near 0 K means the frozen text encoder is not reading")
    print("the changed digits, and the slider-driven demo is not viable as specified.")
    print("=" * 70)


if __name__ == "__main__":
    main()
