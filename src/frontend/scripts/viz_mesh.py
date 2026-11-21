#!/usr/bin/env python3
"""
Mesh quality visualization using pyvista.
Renders STL files + OpenFOAM boundary patches, outputs images.
"""
import sys
import os
import numpy as np
from pathlib import Path

# Offscreen rendering
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")

import pyvista as pv

CASE_DIR = Path("/mnt/lv/litianyu/mycode/dcdtuni/case/V5_CFD_Station/Mesh")
TRI_DIR = CASE_DIR / "constant" / "triSurface"
OUT_DIR = Path("/mnt/lv/litianyu/mycode/dcdtuni/viz_out")
OUT_DIR.mkdir(exist_ok=True)

pv.global_theme.background = "white"
pv.global_theme.font.color = "black"

# ── colour palette ─────────────────────────────────────────────────────────
COLORS = {
    "rack":    "#1f77b4",   # blue
    "acu":     "#ff7f0e",   # orange
    "baffle":  "#2ca02c",   # green
    "floor":   "#d62728",   # red
    "room":    "#9467bd",   # purple
    "leakage": "#8c564b",   # brown
    "other":   "#7f7f7f",   # grey
}

def color_for(name: str) -> str:
    n = name.lower()
    if "rack" in n or "hollow" in n:  return COLORS["rack"]
    if "acu"  in n:                   return COLORS["acu"]
    if "baffle" in n or "wall_baffle" in n: return COLORS["baffle"]
    if "floor" in n or "opening" in n: return COLORS["floor"]
    if "room"  in n:                  return COLORS["room"]
    if "leakage" in n:                return COLORS["leakage"]
    return COLORS["other"]


def flatten_multiblock(mb, prefix=""):
    """Recursively yield (name, dataset) from a MultiBlock."""
    for i in range(mb.n_blocks):
        name = mb.get_block_name(i) or f"block_{i}"
        full = f"{prefix}/{name}" if prefix else name
        block = mb[i]
        if block is None:
            continue
        if isinstance(block, pv.MultiBlock):
            yield from flatten_multiblock(block, full)
        else:
            yield full, block


# ── 1. STL visualisation ───────────────────────────────────────────────────
print("=== Rendering STL files ===")
stl_files = sorted(TRI_DIR.glob("*.stl"))

pl = pv.Plotter(off_screen=True, window_size=(1920, 1080))
pl.set_background("white")

for stl_path in stl_files:
    try:
        mesh = pv.read(str(stl_path))
        name = stl_path.stem
        c = color_for(name)
        opacity = 0.3 if "room" in name.lower() else 0.85
        pl.add_mesh(mesh, color=c, opacity=opacity, label=name,
                    show_edges=False, smooth_shading=True)
        b = mesh.bounds
        nc = mesh.n_cells
        print(f"  {name}: {mesh.n_points} pts, {nc} cells, "
              f"bounds X[{b[0]:.2f},{b[1]:.2f}] Y[{b[2]:.2f},{b[3]:.2f}] Z[{b[4]:.2f},{b[5]:.2f}]")
    except Exception as e:
        print(f"  ERROR loading {stl_path.name}: {e}")

pl.add_legend(face=None, size=(0.25, 0.6), loc="upper right")
pl.camera_position = "iso"
pl.reset_camera()
out = OUT_DIR / "01_all_stl.png"
pl.screenshot(str(out))
print(f"Saved: {out}")
pl.close()

# ── 2. Equipment-only STL (no room, no leakage) ────────────────────────────
print("\n=== Rendering equipment STL (no room/leakage) ===")
pl2 = pv.Plotter(off_screen=True, window_size=(1920, 1080))
pl2.set_background("white")

for stl_path in stl_files:
    name = stl_path.stem
    if "room" in name.lower() or "leakage" in name.lower():
        continue
    try:
        mesh = pv.read(str(stl_path))
        c = color_for(name)
        pl2.add_mesh(mesh, color=c, opacity=0.85, label=name,
                     show_edges=True, edge_color="black", line_width=0.5,
                     smooth_shading=True)
    except Exception as e:
        print(f"  ERROR {stl_path.name}: {e}")

pl2.add_legend(face=None, size=(0.25, 0.6), loc="upper right")
pl2.camera_position = "iso"
pl2.reset_camera()
out2 = OUT_DIR / "02_equipment_stl.png"
pl2.screenshot(str(out2))
print(f"Saved: {out2}")

pl2.camera_position = "xy"
pl2.reset_camera()
out2b = OUT_DIR / "02b_equipment_stl_topdown.png"
pl2.screenshot(str(out2b))
print(f"Saved: {out2b}")
pl2.close()

# ── 3. OpenFOAM mesh boundary patches ─────────────────────────────────────
print("\n=== Rendering OpenFOAM boundary patches ===")
foam_file = CASE_DIR / "case.foam"
if foam_file.exists():
    try:
        reader = pv.OpenFOAMReader(str(foam_file))
        print(f"  Time values: {reader.time_values}")
        print(f"  Patch names: {reader.patch_array_names[:10]} ...")

        reader.enable_all_patch_arrays()
        reader.set_active_time_value(reader.time_values[0] if reader.time_values else 0)
        foam_mesh = reader.read()

        skip_keywords = ["internalMesh", "room_wall", "cyclic", "group"]

        pl3 = pv.Plotter(off_screen=True, window_size=(1920, 1080))
        pl3.set_background("white")
        added = 0

        for full_name, block in flatten_multiblock(foam_mesh):
            # skip internalMesh and room walls
            if any(kw.lower() in full_name.lower() for kw in skip_keywords):
                continue
            if block.n_points == 0:
                continue
            short = full_name.split("/")[-1]
            c = color_for(short)
            pl3.add_mesh(block, color=c, opacity=0.85, label=short[:35],
                         show_edges=True, edge_color="black", line_width=0.5,
                         smooth_shading=True)
            b = block.bounds
            print(f"  Patch '{short}': {block.n_points} pts, {block.n_cells} cells, "
                  f"Z[{b[4]:.2f},{b[5]:.2f}]")
            added += 1

        print(f"  Total patches rendered: {added}")
        pl3.add_legend(face=None, size=(0.3, 0.85), loc="upper right")
        pl3.camera_position = "iso"
        pl3.reset_camera()
        out3 = OUT_DIR / "03_foam_patches.png"
        pl3.screenshot(str(out3))
        print(f"Saved: {out3}")

        pl3.camera_position = "xy"
        pl3.reset_camera()
        out3b = OUT_DIR / "03b_foam_patches_topdown.png"
        pl3.screenshot(str(out3b))
        print(f"Saved: {out3b}")

        # front view (looking along +Y)
        pl3.camera_position = [(0, -50, 1.5), (0, 0, 1.5), (0, 0, 1)]
        pl3.reset_camera()
        out3c = OUT_DIR / "03c_foam_patches_front.png"
        pl3.screenshot(str(out3c))
        print(f"Saved: {out3c}")
        pl3.close()

    except Exception as e:
        import traceback
        print(f"  ERROR reading OpenFOAM mesh: {e}")
        traceback.print_exc()

    # ── 4. internal mesh Z-slice + equipment overlay ───────────────────────
    print("\n=== Rendering internal mesh slice ===")
    try:
        reader2 = pv.OpenFOAMReader(str(foam_file))
        reader2.enable_all_patch_arrays()
        reader2.set_active_time_value(reader2.time_values[0] if reader2.time_values else 0)
        foam2 = reader2.read()

        internal = None
        for full_name, block in flatten_multiblock(foam2):
            if "internalMesh" in full_name:
                internal = block
                break

        if internal and internal.n_points > 0:
            zc = (internal.bounds[4] + internal.bounds[5]) / 2
            print(f"  Internal mesh: {internal.n_points} pts, {internal.n_cells} cells, slicing at Z={zc:.2f}")
            sliced = internal.slice(normal="z", origin=(0, 0, zc))

            pl4 = pv.Plotter(off_screen=True, window_size=(1920, 1080))
            pl4.set_background("white")
            pl4.add_mesh(sliced, color="#dddddd", show_edges=True,
                         edge_color="#999999", line_width=0.3, opacity=0.5)

            for full_name, block in flatten_multiblock(foam2):
                if any(kw in full_name.lower() for kw in ["internalmesh", "room_wall", "cyclic", "group"]):
                    continue
                if block.n_points == 0:
                    continue
                short = full_name.split("/")[-1]
                c = color_for(short)
                pl4.add_mesh(block, color=c, opacity=0.9, smooth_shading=True)

            pl4.camera_position = "xy"
            pl4.reset_camera()
            out4 = OUT_DIR / "04_internal_slice_Z.png"
            pl4.screenshot(str(out4))
            print(f"Saved: {out4}")
            pl4.close()
        else:
            print("  internalMesh not found or empty")

    except Exception as e:
        import traceback
        print(f"  ERROR slicing internal mesh: {e}")
        traceback.print_exc()
else:
    print(f"  case.foam not found: {foam_file}")

print("\nDone. Images in:", OUT_DIR)
