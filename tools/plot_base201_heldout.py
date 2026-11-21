#!/usr/bin/env python3
"""Held-out sensors of a Base201 transient inversion over time: measured against the model at the prior and at the
posterior parameters, one panel per sensor, from the run's ``sensors.csv`` (``tools/base201_transient_inversion.py``).

    python tools/plot_base201_heldout.py _tmp/base201-transient/<run>     # -> <run>/heldout_sensors.{pdf,png}
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

STYLE = {"font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"], "font.size": 7,
         "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "xtick.major.width": 0.6,
         "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5, "legend.frameon": False,
         "pdf.fonttype": 42, "savefig.dpi": 300}
C_MEAS, C_PRIOR, C_POST = "#222222", "#9e9e9e", "#1f6fb4"


def main(run: Path, group: str = "held_out") -> Path:
    run = Path(run)
    summ = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    series = defaultdict(lambda: {"t": [], "m": [], "a": [], "b": []})
    with open(run / "sensors.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["group"] != group:
                continue
            s = series[r["name"]]
            s["t"].append(float(r["t_s"]) / 60.0)
            for key, col in (("m", "measured_C"), ("a", "prior_C"), ("b", "post_C")):
                v = r[col]
                s[key].append(float(v) if v not in ("", "nan") else math.nan)
    names = sorted(series, key=lambda n: (n.rstrip("0123456789"), int("".join(c for c in n if c.isdigit()) or 0)))
    nc = 6
    nr = math.ceil(len(names) / nc)
    plt.rcParams.update(STYLE)
    fig, axes = plt.subplots(nr, nc, figsize=(7.2, 1.05 * nr + 0.5), sharex=True)
    axes = np.atleast_2d(axes)
    for i, ax in enumerate(axes.flat):
        if i >= len(names):
            ax.axis("off")
            continue
        s = series[names[i]]
        ax.plot(s["t"], s["a"], color=C_PRIOR, lw=0.8, ls="--")
        ax.plot(s["t"], s["b"], color=C_POST, lw=1.0)
        ax.plot(s["t"], s["m"], "o", color=C_MEAS, ms=1.6, mew=0)
        ax.set_title(names[i].replace("TemperatureSensor", "S "), fontsize=6, pad=1.5)
        ax.tick_params(labelsize=5.5, pad=1)
        if i % nc == 0:
            ax.set_ylabel("T (°C)", fontsize=6, labelpad=1)
        if i >= len(names) - nc:
            ax.set_xlabel("t (min)", fontsize=6, labelpad=1)
    h = [plt.Line2D([], [], color=C_MEAS, marker="o", ls="", ms=2.5), plt.Line2D([], [], color=C_PRIOR, ls="--"),
         plt.Line2D([], [], color=C_POST)]
    a, b = summ.get(f"rmse_{group}_prior"), summ.get(f"rmse_{group}_post")
    fig.legend(h, ["measured", f"prior  (RMSE {a:.2f} K)", f"posterior  (RMSE {b:.2f} K)"], loc="upper center",
               ncol=3, fontsize=6.5, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.97), h_pad=0.4, w_pad=0.3)
    out = run / f"{'heldout' if group == 'held_out' else group}_sensors"
    fig.savefig(out.with_suffix(".pdf"))
    fig.savefig(out.with_suffix(".png"))
    plt.close(fig)
    return out.with_suffix(".png")


if __name__ == "__main__":
    print(main(Path(sys.argv[1]), *(sys.argv[2:3] or [])))
