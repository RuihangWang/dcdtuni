"""Compute scalar ranges for colormap domains from a CFD internal.vtu."""
import json
import sys
import numpy as np
import pyvista as pv


def field_ranges(vtu_path: str) -> dict:
    g = pv.read(vtu_path)
    # prefer cell data; fall back to point data
    def _arr(name):
        if name in g.cell_data:
            return np.asarray(g.cell_data[name])
        if name in g.point_data:
            return np.asarray(g.point_data[name])
        raise KeyError(f"field {name!r} not found in {vtu_path}")

    t = _arr("T").astype(float)
    u = _arr("U").astype(float)
    umag = np.linalg.norm(u, axis=1) if u.ndim == 2 else np.abs(u)
    return {
        "T": (float(np.nanmin(t)), float(np.nanmax(t))),
        "Umag": (float(np.nanmin(umag)), float(np.nanmax(umag))),
    }


if __name__ == "__main__":
    path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    r = field_ranges(path)
    print(json.dumps(r, indent=2))
    if out:
        with open(out, "w") as f:
            json.dump(r, f)
            f.write("\n")
