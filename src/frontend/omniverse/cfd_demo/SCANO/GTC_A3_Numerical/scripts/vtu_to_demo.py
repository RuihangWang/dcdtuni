#!/usr/bin/env python3
"""Adapt a C1 prediction VTU into the shape the Omniverse demo already knows how to render.

Measured differences between the two artefacts (both verified on real files):

    AI prediction  case0.vtu     344,629 cells   bounds x[0,31] y[0,19]   z[0,5]
                                 CELL arrays  pred_velocity / pred_pressure / pred_Temperature
                                              (+ GT_* = the BASE case's solution)
    demo volume    internal.vtu  1,353,400 cells bounds x[0,31] y[-0,5]   z[0,19.2]
                                 POINT arrays T / U / Umag / p / p_rgh / k / ...

So four things must change:

1. **Axis frame.** The solver mesh is Z-up (OpenFOAM); the demo scene is Y-up (USD).
   Room depth sits in AI-y (0..19) but demo-z (0..19.2), and height in AI-z but demo-y.
   Swapping y and z is a REFLECTION (determinant -1), not a rotation, so it would mirror
   the room. We therefore apply the proper rotation x,y,z -> x,z,-y and then translate
   the depth axis back to positive. Getting this wrong renders a physically plausible but
   mirrored field, which reads as a model bug rather than a transform bug — verify
   visually against the racks before trusting any field.

2. **Velocity is a vector** and must be rotated with the same matrix as the points, not
   merely renamed. A renamed-but-unrotated U points the flow the wrong way while the
   temperature looks fine, which is a nasty failure to spot.

3. **Cell -> point data.** The demo's transfer function and the IndeX voxeliser read
   point arrays named T / Umag.

4. **Drop GT_***. Those are the BASE case's converged fields, unrelated to the user's
   slider values. Left in, they would be read as a validation reference.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pyvista as pv


def to_demo_frame(pts: np.ndarray, depth_extent: float | None = None) -> np.ndarray:
    """Z-up (solver) -> Y-up (USD), as a proper rotation about +x.

    (x, y, z) -> (x, z, -y), then shift -y back into [0, depth].
    """
    out = np.empty_like(pts)
    out[:, 0] = pts[:, 0]
    out[:, 1] = pts[:, 2]
    out[:, 2] = -pts[:, 1]
    if depth_extent is None:
        depth_extent = float(pts[:, 1].max())
    out[:, 2] += depth_extent
    return out


def rotate_vec(v: np.ndarray) -> np.ndarray:
    """Same rotation, without the translation (vectors do not translate)."""
    out = np.empty_like(v)
    out[:, 0] = v[:, 0]
    out[:, 1] = v[:, 2]
    out[:, 2] = -v[:, 1]
    return out


def resample_to_image(mesh, spacing: float = 0.2):
    """Resample onto a regular grid — REQUIRED for the volume to actually render.

    IndeX voxelises a structured image directly, but hands back an EMPTY grid for an
    unstructured mesh: the backend still logs "volume rebuilt … visible=True", yet the
    receipt carries ``extent=None`` and the viewport shows nothing. That is exactly what
    happened with the raw 344 k-cell prediction, and it is the same failure the demo's
    own geometry.ts documents for the 1.35 M-cell internal.vtu. The shipped internal.vti
    is that file resampled at ~0.2 m, so we do the same here.

    A trusted log line plus a blank frame is precisely why the render must be read back
    visually and not inferred from the receipt.
    """
    import pyvista as pv
    from scipy.spatial import cKDTree

    b = mesh.bounds
    dims = tuple(max(2, int(round((b[2 * i + 1] - b[2 * i]) / spacing)) + 1)
                 for i in range(3))
    grid = pv.ImageData(dimensions=dims, spacing=(spacing,) * 3,
                        origin=(b[0], b[2], b[4]))

    # NEAREST NEIGHBOUR, not VTK's probe filter. Both vtkProbeFilter (via
    # ImageData.sample) and vtkResampleToImage walk a cell locator over a 344 k-cell
    # unstructured mesh for every one of ~390 k grid points, which measured 163 s and
    # 230 s respectively — unusable behind an interactive button. A KD-tree over the
    # source points answers the same query in about a second, and for a volume RENDER
    # nearest-neighbour is adequate: the grid spacing is comparable to the mesh cell
    # size, so trilinear weights would not add visible detail.
    src_pts = np.asarray(mesh.points)
    tree = cKDTree(src_pts)
    # A grid point further than this from any mesh point is inside a rack or outside the
    # room — there is no fluid there to interpolate from.
    cutoff = spacing * 1.5
    dist, idx = tree.query(np.asarray(grid.points), k=1,
                           distance_upper_bound=cutoff, workers=-1)
    outside = ~np.isfinite(dist)
    idx = np.where(outside, 0, idx)

    for name in list(mesh.point_data.keys()):
        a = np.asarray(mesh.point_data[name])
        v = a[idx]
        if outside.any():
            # Park the no-fluid voxels at the bottom of the scale rather than at 0.
            # Zeros would drag the temperature domain from 292..319 K down to 0..319 K
            # and paint every solid object the coldest colour — a field that looks
            # physically wrong rather than simply empty.
            if v.ndim == 1:
                inside = v[~outside]
                v = v.copy(); v[outside] = float(inside.min()) if inside.size else 0.0
            else:
                v = v.copy(); v[outside] = 0.0
        grid.point_data[name] = v

    if outside.any():
        print(f"[adapt] {int(outside.sum())}/{outside.size} grid points outside the "
              f"fluid mesh filled with the field minimum", flush=True)
    return grid, dims


def _resolve_depth(src: str, mesh, depth):
    """The solver-frame y_max used to translate the depth axis into the demo frame.

    THIS IS THE SILENT-FAILURE HAZARD. ``to_demo_frame`` maps z_demo = depth - y_solver, so
    ``depth`` is a property of the FULL domain, not of the file in front of us. Deriving it
    from ``mesh.bounds[3]`` is right for a whole-domain export and WRONG for any subset: an
    ROI whose y_max is 12 m in a 19 m room gets translated by 12 instead of 19 and lands
    7 m off along the demo's depth axis — with a healthy-looking log, the correct cell
    count and physically plausible values. Nothing downstream can detect it.

    So an ROI export writes a receipt carrying the FULL domain's depth, and that number
    wins here. Resolution order: explicit argument, then the receipt, then the file's own
    bounds — the last being exactly the previous behaviour, which is what a whole-domain
    export still takes.
    """
    if depth is not None:
        return float(depth), "argument"
    sidecar = os.path.splitext(src)[0] + ".scano_roi.json"
    if os.path.exists(sidecar):
        try:
            with open(sidecar) as f:
                info = json.load(f)
            d = info.get("depth")
            if d is None and info.get("full_bounds_solver"):
                d = info["full_bounds_solver"][3]
            if d is None:
                raise KeyError("no 'depth' or 'full_bounds_solver'")
            return float(d), f"receipt {os.path.basename(sidecar)}"
        except Exception as e:
            # Falling back to the file's own bounds here would reintroduce the exact bug
            # the receipt exists to prevent, and do it quietly. Refuse instead.
            raise ValueError(f"unusable ROI receipt {sidecar}: {e}") from e
    return float(mesh.bounds[3]), "file bounds"


def adapt(src: str, dst: str, keep_gt: bool = False, depth=None) -> dict:
    mesh = pv.read(src)
    depth, depth_src = _resolve_depth(src, mesh, depth)
    if depth_src != "file bounds":
        print(f"[adapt] depth={depth:.4f} m from {depth_src} "
              f"(this file's own y_max is {float(mesh.bounds[3]):.4f})", flush=True)

    if not keep_gt:
        for k in list(mesh.cell_data.keys()):
            if k.startswith("GT_"):
                del mesh.cell_data[k]

    # Rename into the demo's vocabulary BEFORE interpolating, so the point arrays land
    # with the right names.
    if "pred_Temperature" in mesh.cell_data:
        mesh.cell_data["T"] = np.asarray(mesh.cell_data["pred_Temperature"], dtype=np.float32)
        del mesh.cell_data["pred_Temperature"]
    if "pred_velocity" in mesh.cell_data:
        u = np.asarray(mesh.cell_data["pred_velocity"], dtype=np.float32)
        mesh.cell_data["U"] = rotate_vec(u)
        mesh.cell_data["Umag"] = np.linalg.norm(u, axis=1).astype(np.float32)
        del mesh.cell_data["pred_velocity"]
    if "pred_pressure" in mesh.cell_data:
        mesh.cell_data["p"] = np.asarray(mesh.cell_data["pred_pressure"], dtype=np.float32)
        del mesh.cell_data["pred_pressure"]
    # Provenance only. It is an integer cpd index, so cell->point averaging and then
    # nearest-neighbour resampling would turn it into a fractional id that means nothing
    # and would be handed to the transfer function as if it were a field.
    if "cpd_id" in mesh.cell_data:
        del mesh.cell_data["cpd_id"]

    mesh.points = to_demo_frame(np.asarray(mesh.points, dtype=np.float64), depth)

    out = mesh.cell_data_to_point_data()
    # IndeX needs a structured image; an unstructured grid voxelises to nothing.
    if dst.endswith(".vti"):
        out, dims = resample_to_image(out)
        print(f"[adapt] resampled to image grid {dims}", flush=True)
    out.save(dst)

    stats = {}
    for name in ("T", "Umag"):
        if name in out.point_data:
            a = np.asarray(out.point_data[name], dtype=np.float64)
            stats[name] = {
                "min": float(a.min()), "max": float(a.max()),
                "p1": float(np.percentile(a, 1)), "p99": float(np.percentile(a, 99)),
                "mean": float(a.mean()),
            }
    info = {
        "source": src,
        "n_points": int(out.n_points),
        "n_cells": int(out.n_cells),
        "bounds": [float(b) for b in out.bounds],
        "point_arrays": list(out.point_data.keys()),
        "stats": stats,
        "depth": float(depth),
        "depth_source": depth_src,
    }
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--keep-gt", action="store_true")
    ap.add_argument("--ranges-out", help="write a ranges.json sidecar next to dst")
    ap.add_argument("--depth", type=float, default=None,
                    help="FULL-domain y_max in the solver frame. Only needed for a subset "
                         "whose .scano_roi.json receipt is missing — without it the depth "
                         "translation is taken from the subset's own bounds and the field "
                         "lands in the wrong place.")
    args = ap.parse_args()

    info = adapt(args.src, args.dst, keep_gt=args.keep_gt, depth=args.depth)
    print(json.dumps(info, indent=2))

    if args.ranges_out:
        st = info["stats"]
        ranges = {
            # Pin the display domain to p1..p99, matching the decision already baked into
            # the demo's volume transfer function (pinned, not auto-fit) so successive
            # predictions stay visually comparable.
            "T": [st["T"]["p1"], st["T"]["p99"]] if "T" in st else None,
            "U": [st["Umag"]["p1"], st["Umag"]["p99"]] if "Umag" in st else None,
            "_actual": st,
        }
        with open(args.ranges_out, "w") as f:
            json.dump(ranges, f, indent=2)
        print(f"wrote {args.ranges_out}")


if __name__ == "__main__":
    main()
