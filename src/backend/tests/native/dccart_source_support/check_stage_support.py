"""Compare a dcCart iface_dbg stage's tagged faces with source STL triangles.

This deliberately checks the exported source geometry, not a mesher-welded
copy. It is a necessary face-centroid condition, not full polygon coverage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from backend.aircooling.runner.tools.mesh_source_support_probe import (
    _read_ascii_stl_solid, projected_support,
)


def inspect(stage: Path, tri_surface: Path, mapping: dict[int, tuple[str, str]],
            roi: tuple[float, ...] | None = None, tolerance_m: float = 1e-5) -> dict:
    triangles = {
        zone: _read_ascii_stl_solid(tri_surface / stl, solid)
        for zone, (stl, solid) in mapping.items()
    }
    samples = {}
    for line in stage.read_text().splitlines():
        if " | own=" not in line:
            continue
        match = re.search(r"patch=(-?\d+) zone=(-?\d+)", line)
        if not match:
            raise ValueError(f"invalid stage face: {line}")
        zone = int(match.group(2))
        if zone not in mapping:
            continue
        polygon = tuple(tuple(map(float, p.split()))
                        for p in re.findall(r"\(([-\d.eE+ ]+)\)", line))
        if len(polygon) < 3:
            raise ValueError(f"stage face has fewer than three vertices: {line}")
        centroid = tuple(sum(p[i] for p in polygon) / len(polygon) for i in range(3))
        if roi and not all(roi[i] <= centroid[i] <= roi[i + 3] for i in range(3)):
            continue
        key = (zone, tuple(sorted(polygon)))
        if key in samples:
            continue
        best = min((projected_support(centroid, tri) for tri in triangles[zone]),
                   key=lambda d: (d.in_plane_gap_m, d.plane_offset_m))
        samples[key] = {
            "zone": zone, "sourceStl": mapping[zone][0], "sourceSolid": mapping[zone][1],
            "centroidM": centroid, "inPlaneGapM": best.in_plane_gap_m,
            "planeOffsetM": best.plane_offset_m,
        }
    if not samples:
        raise ValueError("no mapped tagged faces in the requested stage/ROI")
    bad = sorted((v for v in samples.values() if v["inPlaneGapM"] > tolerance_m),
                 key=lambda v: -v["inPlaneGapM"])
    return {"stage": str(stage), "sampleCount": len(samples), "outsideCount": len(bad),
            "maxInPlaneGapM": max(v["inPlaneGapM"] for v in samples.values()),
            "toleranceM": tolerance_m, "outside": bad}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--tri-surface", type=Path, required=True)
    parser.add_argument("--map", action="append", required=True,
                        help="ZONE:FILE:SOLID, repeated for each explicit source sheet")
    parser.add_argument("--roi", type=float, nargs=6)
    parser.add_argument("--tolerance-m", type=float, default=1e-5)
    args = parser.parse_args()
    mapping = {}
    for item in args.map:
        zone_text, stl, solid = item.split(":", 2)
        zone = int(zone_text)
        if zone in mapping:
            raise ValueError(f"duplicate zone {zone}")
        mapping[zone] = stl, solid
    result = inspect(args.stage, args.tri_surface, mapping,
                     tuple(args.roi) if args.roi else None, args.tolerance_m)
    print(json.dumps(result, indent=2))
    return 2 if result["outsideCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
