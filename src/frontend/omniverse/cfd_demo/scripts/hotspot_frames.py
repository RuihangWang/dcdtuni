#!/usr/bin/env python3
"""Turn the three solved anchors into the frames the Omniverse recorder renders.

`hotspot_anchors.py` leaves three steady solutions on one mesh. This converts each
to VTK, checks they really do share cells, and writes one `.vtu` per rendered frame:

    f000          S0                      held
    f001..f014    S0 -> S1  (smoothstep)  the hotspot growing
    f015          S1                      held
    f016..f029    S1 -> S2  (smoothstep)  the intervention
    f030          S2                      held

31 files for a 6 s animation. The held segments are one image each -- the assembler
repeats them -- and the mp4 blends adjacent frames up to 24 fps, so nothing here
needs to be rendered at playback rate.

Per point,  F(a) = (1-a)*A + a*B  for T and U, with a = smoothstep so the motion
eases in and out. Divergence is a linear operator, so a convex combination of two
divergence-free fields is still divergence-free: the in-between velocity fields
satisfy continuity and their streamlines are real integral curves. They do not
satisfy momentum or energy, which is why only the three anchors are labelled as
solved states.

`Umag` is written as its own scalar array. IndeX's `vector_magnitude` is unreliable
on this path -- the shipped `data/internal.vtu` carries a materialised `Umag` for
the same reason.

Also emitted, into `frames.json`:
  * the p1-p99 temperature domain over ALL anchors, which the recorder pins so the
    colour map cannot renormalise the hotspot away frame by frame
  * the measured S1-vs-S0 peak rise, which fills the caption instead of a guess

    python3 hotspot_frames.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pyvista as pv

REPO = pathlib.Path(__file__).resolve().parents[5]
CASE = pathlib.Path(os.environ.get("HOTSPOT_CASE", REPO / "case/GTC"))
# Artefacts live under case/GTC/forOVdemo/, which .gitignore excludes: the anchors, the
# 31 interpolated frames and the viewport shots come to ~6.6 GB per scenario and have no
# business in git. Only the finished videos (copied into the project page's assets), the
# ACU position file and this code are tracked.
ART = pathlib.Path(os.environ.get("HOTSPOT_ART", CASE / "forOVdemo"))
WORK = pathlib.Path(os.environ.get("HOTSPOT_OUT", ART / "hotspot_anchors"))
FRAMES = pathlib.Path(os.environ.get("HOTSPOT_FRAMES", ART / "hotspot_frames"))
STEPS = int(os.environ.get("HOTSPOT_STEPS", "14"))   # frames per transition

SEGMENTS = [
    ("hold_S0", "S0", "S0", 1, "正常运行 · 6 台 ACU"),
    ("fail", "S0", "S1", STEPS, "3 台 ACU 故障"),
    ("hold_S1", "S1", "S1", 1, "热点形成 · 峰值 +{peak:.1f} K"),
    ("fix", "S1", "S2", STEPS, "干预 · 风量 ×1.8、送风 15 °C"),
    ("hold_S2", "S2", "S2", 1, "热点消除"),
]


def log(m):
    print(f"[frames] {m}", flush=True)


def smoothstep(x):
    return x * x * (3.0 - 2.0 * x)


def to_vtk(anchor, log_dir):
    """foamToVTK the anchor's latest time, returning its internal.vtu."""
    dest = WORK / anchor
    cached = dest / "internal.vtu"
    if cached.exists():
        log(f"{anchor}: reusing {cached.name}")
        return cached
    log(f"{anchor}: foamToVTK on {log_dir}")
    subprocess.run(
        ["foamToVTK", "-case", str(log_dir), "-latestTime",
         "-fields", "(T U)", "-no-finite-area"],
        check=True, capture_output=True)
    produced = sorted(pathlib.Path(log_dir).glob("VTK/*/internal.vtu"))
    if not produced:
        raise SystemExit(f"{anchor}: foamToVTK produced no internal.vtu")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced[-1], cached)
    log(f"{anchor}: {cached} ({cached.stat().st_size / 1e6:.0f} MB)")
    return cached


def field(mesh, assoc, *names):
    """Fetch an array from point_data or cell_data.

    Both associations matter and for different consumers. foamToVTK writes T and U as
    CELL data (OpenFOAM is finite-volume) and as point-interpolated data; kit-cae's
    voxeliser reads the CELL arrays. A frame carrying point data only rebuilds without
    error, reports visible=True, and renders NOTHING -- which is exactly how this was
    found, after a recording run came back as bare geometry.
    """
    data = mesh.point_data if assoc == "point" else mesh.cell_data
    for n in names:
        if n in data:
            return np.asarray(data[n])
    raise SystemExit(f"none of {names} in {assoc}_data {list(data)}")


def main():
    manifest = json.loads((WORK / "anchors.json").read_text())
    missing = [a for a in ("S0", "S1", "S2") if a not in manifest]
    if missing:
        raise SystemExit(f"anchors not solved yet: {missing}")

    meshes, T, U, Tc, Uc = {}, {}, {}, {}, {}
    for anchor in ("S0", "S1", "S2"):
        m = pv.read(to_vtk(anchor, manifest[anchor]["logDir"]))
        meshes[anchor] = m
        T[anchor] = field(m, "point", "T").astype(np.float32)
        U[anchor] = field(m, "point", "U").astype(np.float32)
        Tc[anchor] = field(m, "cell", "T").astype(np.float32)
        Uc[anchor] = field(m, "cell", "U").astype(np.float32)

    # The interpolation is per point, so the three grids must be the same grid.
    ref = meshes["S0"]
    for anchor in ("S1", "S2"):
        m = meshes[anchor]
        if m.n_points != ref.n_points:
            raise SystemExit(f"{anchor}: {m.n_points} points vs S0 {ref.n_points} "
                             "-- resample onto S0 before interpolating")
        drift = float(np.abs(m.points - ref.points).max())
        if drift > 0:
            raise SystemExit(f"{anchor}: points differ by up to {drift:g} m")
        log(f"{anchor}: grid matches S0 ({m.n_points} points)")

    # The number the S1 caption states, and the fastest way to catch a dead S1.
    d10 = T["S1"] - T["S0"]
    d12 = T["S1"] - T["S2"]
    peak = float(np.percentile(d10, 99))
    log(f"S1 - S0 : mean {d10.mean():+.2f} K, p99 {peak:+.2f} K, max {d10.max():+.2f} K")
    log(f"S1 - S2 : mean {d12.mean():+.2f} K, p99 {np.percentile(d12, 99):+.2f} K")
    for a in ("S0", "S1", "S2"):
        t = T[a] - 273.15
        log(f"{a}: T {t.min():.1f} .. {t.max():.1f} °C  (p99 {np.percentile(t, 99):.1f})")
    if peak < 2.0:
        log("WARNING: S1 is barely warmer than S0 -- the animation will read flat")

    # One domain for every frame of both animations. Auto-ranging per frame is
    # exactly how a real change renders as no change.
    allT = np.concatenate([T["S0"], T["S1"], T["S2"]])
    vmin, vmax = (float(np.percentile(allT, 1)), float(np.percentile(allT, 99)))
    log(f"pinned colour domain: {vmin:.2f} .. {vmax:.2f} K "
        f"({vmin - 273.15:.1f} .. {vmax - 273.15:.1f} °C)")

    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in FRAMES.glob("f*.vtu"):
        old.unlink()

    frames, idx = [], 0
    out = ref.copy()
    out.clear_data()
    for name, a, b, n, caption in SEGMENTS:
        for k in range(n):
            alpha = smoothstep((k + 1) / n) if a != b else 0.0
            t = (1 - alpha) * T[a] + alpha * T[b]
            u = (1 - alpha) * U[a] + alpha * U[b]
            tc = (1 - alpha) * Tc[a] + alpha * Tc[b]
            uc = (1 - alpha) * Uc[a] + alpha * Uc[b]
            out.point_data["T"] = t
            out.point_data["U"] = u
            out.point_data["Umag"] = np.linalg.norm(u, axis=1).astype(np.float32)
            out.cell_data["T"] = tc
            out.cell_data["U"] = uc
            out.cell_data["Umag"] = np.linalg.norm(uc, axis=1).astype(np.float32)
            path = FRAMES / f"f{idx:03d}.vtu"
            out.save(path, binary=True)
            frames.append({
                "index": idx, "file": path.name, "segment": name,
                "from": a, "to": b, "alpha": round(float(alpha), 4),
                "caption": caption.format(peak=peak),
                "peakC": round(float(t.max() - 273.15), 2),
            })
            log(f"f{idx:03d} {name:8} {a}->{b} a={alpha:.3f} "
                f"peak {t.max() - 273.15:.1f} °C")
            idx += 1

    (FRAMES / "frames.json").write_text(json.dumps({
        "anchors": {k: manifest[k] for k in ("S0", "S1", "S2")},
        "colourDomainK": [vmin, vmax],
        "peakRiseK": round(peak, 2),
        "steps": STEPS,
        "frames": frames,
    }, indent=2, ensure_ascii=False))
    log(f"{len(frames)} frames -> {FRAMES}")


if __name__ == "__main__":
    sys.exit(main())
