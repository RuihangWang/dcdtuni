"""ROI-restricted VTU export: box parsing, frame conversion, cell selection, receipt.

Why this exists
---------------
The demo used to hand the viewer the WHOLE predicted field even when the user had drawn
a region of interest. That is wrong: the ROI is the thing the user asked to look at, so
the ROI is what should be shipped.

Why the INFERENCE is not restricted, only the EXPORT
----------------------------------------------------
Restricting the model's own pivot pool to the box was measured and it FAILS. Commit
8da9599 ("measure contiguous-ROI sampling against the scattered baseline — it FAILS")
records a 3% ROI costing +0.406 nRMSE against a 0.077 fail threshold, with a temperature
error of 1.03 K — larger than the 0.94 K ACU signal the demo exists to show. The
mechanism is in HomoGraph_dataset's pivot-pool docstring: the query basis is pooled
node-set-globally eight times per forward, so a spatially confined pivot sample collapses
the slice basis and can no longer address the boundary the cross-attention injects.

So the model still sees the whole domain, exactly as before, and only the writer selects.
That is EXACT — every exported value is bit-identical to the value the unrestricted export
would have written for the same cell — and it satisfies the requirement literally.

The frame hazard
----------------
The solver mesh is Z-up (OpenFOAM); the demo scene is Y-up (USD). ``vtu_to_demo`` maps
solver -> demo with a proper rotation about +x plus a translation whose magnitude is the
DEPTH of the full domain:

    demo = (x_s,  z_s,  depth - y_s)          depth = full-domain y_max, solver frame

The translation is the trap. A subset's own y_max is smaller than the full domain's, so
anything that recomputes ``depth`` from the subset places it at the wrong depth — with a
log line that looks perfectly healthy. Therefore ``depth`` is resolved ONCE here, from the
full mesh, used for the box conversion, and written into the receipt so the adapter reuses
the identical number instead of re-deriving it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

#: Environment variable carrying the export box. Read by Inferencer._export_case_vtu.
ENV_EXPORT_ROI = "SCANO_EXPORT_ROI"

#: Suffix of the receipt written beside the VTU. vtu_to_demo reads it for ``depth``.
RECEIPT_SUFFIX = ".scano_roi.json"


def receipt_path(vtu_path: str) -> str:
    """Where the receipt for ``vtu_path`` lives. One deterministic rule, both sides."""
    return os.path.splitext(vtu_path)[0] + RECEIPT_SUFFIX


# ── box parsing ──────────────────────────────────────────────────────────────────────

def parse_export_roi(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse the export box. Returns None for "no ROI" (the default full-field export).

    Two accepted spellings:

    * JSON  ``{"min": [x,y,z], "max": [x,y,z], "frame": "demo"}`` — ``frame`` is optional
      and defaults to ``"demo"``, because the box the UI owns is the demo-frame ROI box
      from CfdControls (metres, Y-up).
    * Six comma/semicolon-separated floats ``"xmin,ymin,zmin,xmax,ymax,zmax"`` — the same
      spelling ``SCANO_PIVOT_BOX`` uses in FVdomain/HomoGraph_dataset.py, and read in the
      same frame it uses: the SOLVER frame. Keeping one string format tied to one frame
      is what stops a box being pasted between the two knobs and silently landing rotated.

    Empty / unset / "off" mean no ROI.
    """
    raw = (raw or "").strip()
    if not raw or raw.lower() in ("0", "off", "none", "full"):
        return None

    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise ValueError(f"{ENV_EXPORT_ROI} is not valid JSON: {e}") from e
        lo = obj.get("min")
        hi = obj.get("max")
        if not (isinstance(lo, Sequence) and isinstance(hi, Sequence)
                and len(lo) == 3 and len(hi) == 3):
            raise ValueError(
                f'{ENV_EXPORT_ROI} JSON needs "min" and "max" as 3-element lists; got {obj!r}')
        frame = str(obj.get("frame") or "demo").strip().lower()
    else:
        parts = [p for p in raw.replace(";", ",").split(",") if p.strip()]
        if len(parts) != 6:
            raise ValueError(
                f"{ENV_EXPORT_ROI} must be JSON or 'xmin,ymin,zmin,xmax,ymax,zmax'; got {raw!r}")
        lo, hi = parts[:3], parts[3:]
        frame = "solver"

    if frame not in ("demo", "solver"):
        raise ValueError(f"{ENV_EXPORT_ROI} frame must be demo|solver, got {frame!r}")

    lo = [float(v) for v in lo]
    hi = [float(v) for v in hi]
    # Accept a box given corner-first in either order per axis rather than rejecting it:
    # the UI clamps min<=max, but a hand-typed or gizmo-dragged box need not, and an
    # inverted axis would otherwise select zero cells and read as "the ROI is empty".
    lo, hi = [min(a, b) for a, b in zip(lo, hi)], [max(a, b) for a, b in zip(lo, hi)]
    if not all(hi[i] > lo[i] for i in range(3)):
        raise ValueError(f"{ENV_EXPORT_ROI} has zero extent on some axis: min={lo} max={hi}")
    return {"min": lo, "max": hi, "frame": frame}


# ── frame conversion ─────────────────────────────────────────────────────────────────
# demo = (x_s, z_s, depth - y_s)   <->   solver = (X_d, depth - Z_d, Y_d)
#
# Both are proper rotations about +x plus a translation on one axis, so an axis-aligned
# box stays axis-aligned. The depth axis FLIPS, which is why min/max swap on it — get
# that backwards and the subset lands mirrored about the room's mid-depth, which looks
# like a plausible field in the wrong place rather than like a bug.

def demo_box_to_solver(lo: Sequence[float], hi: Sequence[float], depth: float):
    """Demo-frame AABB -> solver-frame AABB. ``depth`` = FULL-domain y_max (solver)."""
    s_lo = [float(lo[0]), float(depth) - float(hi[2]), float(lo[1])]
    s_hi = [float(hi[0]), float(depth) - float(lo[2]), float(hi[1])]
    return s_lo, s_hi


def solver_box_to_demo(lo: Sequence[float], hi: Sequence[float], depth: float):
    """Solver-frame AABB -> demo-frame AABB. Exact inverse of :func:`demo_box_to_solver`."""
    d_lo = [float(lo[0]), float(lo[2]), float(depth) - float(hi[1])]
    d_hi = [float(hi[0]), float(hi[2]), float(depth) - float(lo[1])]
    return d_lo, d_hi


def resolve_box(roi: Dict[str, Any], depth: float):
    """Return ``(solver_lo, solver_hi, demo_lo, demo_hi)`` for a parsed ROI."""
    if roi["frame"] == "demo":
        s_lo, s_hi = demo_box_to_solver(roi["min"], roi["max"], depth)
        return s_lo, s_hi, list(roi["min"]), list(roi["max"])
    d_lo, d_hi = solver_box_to_demo(roi["min"], roi["max"], depth)
    return list(roi["min"]), list(roi["max"]), d_lo, d_hi


# ── selection ────────────────────────────────────────────────────────────────────────

def select_in_box(centroids: np.ndarray, lo: Sequence[float], hi: Sequence[float]) -> np.ndarray:
    """Indices of rows of ``centroids`` (N,3) whose point lies inside the closed box."""
    c = np.asarray(centroids, dtype=np.float64)
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    return np.flatnonzero(np.all((c >= lo) & (c <= hi), axis=1))


# ── receipt ──────────────────────────────────────────────────────────────────────────

def _fmt(v: Sequence[float]) -> str:
    return "(" + ",".join(f"{float(x):.3f}" for x in v) + ")"


def receipt_line(case_name: str, n_exported: int, n_total: int, demo_lo, demo_hi,
                 solver_lo, solver_hi, depth: float, out_path: str) -> str:
    """One machine-readable line: how many cells of how many, the box, the output path.

    ai_worker re-emits this verbatim into its own log, which the web UI already tails over
    /api/ai-log — so the number the user sees is the number the writer actually wrote,
    not a count re-derived downstream.
    """
    frac = (n_exported / n_total) if n_total else 0.0
    return (f"SCANO_ROI_EXPORT case={case_name} exported={n_exported} of={n_total} "
            f"frac={frac:.6f} box_demo={_fmt(demo_lo)}-{_fmt(demo_hi)} "
            f"box_solver={_fmt(solver_lo)}-{_fmt(solver_hi)} depth={depth:.6f} "
            f"out={out_path}")


def write_receipt(vtu_path: str, payload: Dict[str, Any]) -> str:
    """Write the receipt beside the VTU and return its path."""
    p = receipt_path(vtu_path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w") as f:
        json.dump(payload, f, indent=2)
    return p


def build_receipt(*, case_name: str, vtu_path: str, cpd_ids: np.ndarray, n_cells_total: int,
                  n_cpd_total: int, n_cpd_in_box: int, demo_lo, demo_hi, solver_lo, solver_hi,
                  full_bounds: List[float], depth: float) -> Dict[str, Any]:
    """The receipt dict. ``full_bounds`` is the FULL solver-frame domain AABB, and is the
    reason the adapter never has to guess ``depth`` from a subset."""
    n = int(len(cpd_ids))
    return {
        "roi": True,
        "case": case_name,
        "vtu": vtu_path,
        "n_cells_exported": n,
        "n_cells_total": int(n_cells_total),
        "fraction": (n / n_cells_total) if n_cells_total else 0.0,
        "n_cpd_total": int(n_cpd_total),
        "n_cpd_in_box": int(n_cpd_in_box),
        "box_demo": {"min": [float(v) for v in demo_lo], "max": [float(v) for v in demo_hi]},
        "box_solver": {"min": [float(v) for v in solver_lo], "max": [float(v) for v in solver_hi]},
        # The two numbers vtu_to_demo must not re-derive from the subset.
        "full_bounds_solver": [float(v) for v in full_bounds],
        "depth": float(depth),
        "cpd_id_range": [int(cpd_ids.min()), int(cpd_ids.max())] if n else None,
        "note": ("Inference ran over the WHOLE domain; only the export is restricted. "
                 "Restricting the pivot pool to the box was measured and fails (8da9599)."),
    }
