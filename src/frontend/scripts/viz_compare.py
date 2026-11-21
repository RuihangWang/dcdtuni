#!/usr/bin/env python3
"""Compare reference STLs vs current triSurface STLs."""
import os
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")
import pyvista as pv
from pathlib import Path

REF = Path("/mnt/lv/litianyu/mycode/dcdtuni/case/V5_CFD_Station/ref_STL")
OUT_DIR = Path("/mnt/lv/litianyu/mycode/dcdtuni/viz_out")
OUT_DIR.mkdir(exist_ok=True)

pv.global_theme.background = "white"

COLORS = {
    "rack":    "#1f77b4",
    "acu":     "#ff7f0e",
    "baffle":  "#2ca02c",
    "floor":   "#d62728",
    "room":    "#9467bd",
    "box":     "#17becf",
    "other":   "#7f7f7f",
}

def color_for(name: str) -> str:
    n = name.lower()
    if "rack"    in n: return COLORS["rack"]
    if "acu"     in n: return COLORS["acu"]
    if "baffle"  in n: return COLORS["baffle"]
    if "floor"   in n or "ceiling" in n: return COLORS["floor"]
    if "room"    in n: return COLORS["room"]
    if "box"     in n: return COLORS["box"]
    return COLORS["other"]

print("=== Reference STL bounds ===")
pl = pv.Plotter(off_screen=True, window_size=(1920, 1080))
pl.set_background("white")
for f in sorted(REF.glob("*.stl")):
    try:
        m = pv.read(str(f))
        b = m.bounds
        c = color_for(f.stem)
        print(f"  {f.stem}: cells={m.n_cells}, "
              f"X[{b[0]:.2f},{b[1]:.2f}] Y[{b[2]:.2f},{b[3]:.2f}] Z[{b[4]:.2f},{b[5]:.2f}]")
        opacity = 0.25 if "room" in f.stem.lower() else 0.85
        pl.add_mesh(m, color=c, opacity=opacity, label=f.stem,
                    show_edges=True, edge_color="black", line_width=0.5)
    except Exception as e:
        print(f"  {f.stem}: ERROR {e}")

pl.add_legend(face=None, size=(0.2, 0.5), loc="upper right")
pl.camera_position = "iso"
pl.reset_camera()
out = OUT_DIR / "ref_iso.png"
pl.screenshot(str(out))
print(f"Saved: {out}")

pl.camera_position = "xy"
pl.reset_camera()
out2 = OUT_DIR / "ref_topdown.png"
pl.screenshot(str(out2))
print(f"Saved: {out2}")
pl.close()
print("Done.")
