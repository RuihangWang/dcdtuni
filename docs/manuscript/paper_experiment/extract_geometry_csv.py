#!/usr/bin/env python3
"""Extract geometry CSVs from label cfdproj files for all demo cases.

For each case, reads label/{case}.cfdproj and writes to input/:
  - rack.csv       (rack positions, sizes, orientations)
  - baffle.csv     (baffle positions, sizes, orientations)
  - floor.csv      (floor/ceiling/raised floor info)
  - box.csv        (box/support structures, if any)

Existing ACU.csv and server_power.csv are NOT touched.
"""
import csv
import json
import math
import sys
import pathlib
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src/frontend/scripts"))
from cfdproj_migrate import load_cfdproj

DEMO = Path(__file__).parent


def get_yaw_deg(m):
    q = m.get("transform", {}).get("quaternion", {})
    qy, qw = q.get("y", 0), q.get("w", 1)
    return round(2 * math.atan2(qy, qw) * 180 / math.pi, 2)


def count_patches(m):
    return sum(len(f.get("patches", [])) for f in m.get("faces", {}).values())


def extract_case(case_name):
    label_path = DEMO / case_name / "label" / f"{case_name}.cfdproj"
    input_dir = DEMO / case_name / "input"

    if not label_path.exists():
        print(f"  SKIP {case_name}: no label")
        return

    proj = load_cfdproj(label_path)

    models = proj.get("construction", [])

    racks, baffles, floors, boxes = [], [], [], []

    for m in models:
        kind = m.get("kind", "")
        name = m.get("name", "")
        a = m.get("anchor", {})
        b = m.get("base", {})
        yaw = get_yaw_deg(m)

        if kind == "rack":
            racks.append({
                "rack_name": name,
                "pos_x": round(a.get("x", 0), 4),
                "pos_y": round(a.get("y", 0), 4),
                "pos_z": round(a.get("z", 0), 4),
                "size_W": round(b.get("W", 0), 4),
                "size_D": round(b.get("D", 0), 4),
                "size_H": round(b.get("H", 0), 4),
                "orientation_deg": yaw,
            })
        elif kind == "baffle":
            baffles.append({
                "baffle_name": name,
                "pos_x": round(a.get("x", 0), 4),
                "pos_y": round(a.get("y", 0), 4),
                "pos_z": round(a.get("z", 0), 4),
                "size_W": round(b.get("W", 0), 4),
                "size_H": round(b.get("H", 0), 4),
                "orientation_deg": yaw,
            })
        elif kind == "floor":
            floor_type = "ground"
            if "ceiling" in name.lower() or "ceil" in name.lower():
                floor_type = "ceiling"
            elif "rais" in name.lower() or "raised" in name.lower() or "copy" in name.lower():
                if a.get("y", 0) > 0.05:
                    floor_type = "raised"

            floors.append({
                "floor_name": name,
                "pos_y": round(a.get("y", 0), 4),
                "size_W": round(b.get("W", 0), 4),
                "size_D": round(b.get("D", 0), 4),
                "type": floor_type,
                "n_patches": count_patches(m),
            })
        elif kind == "box":
            boxes.append({
                "box_name": name,
                "pos_x": round(a.get("x", 0), 4),
                "pos_y": round(a.get("y", 0), 4),
                "pos_z": round(a.get("z", 0), 4),
                "size_W": round(b.get("W", 0), 4),
                "size_D": round(b.get("D", 0), 4),
                "size_H": round(b.get("H", 0), 4),
                "orientation_deg": yaw,
            })

    def write_csv(filename, rows, fieldnames):
        if not rows:
            return 0
        path = input_dir / filename
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        return len(rows)

    nr = write_csv("rack.csv", racks,
                    ["rack_name", "pos_x", "pos_y", "pos_z", "size_W", "size_D", "size_H", "orientation_deg"])
    nb = write_csv("baffle.csv", baffles,
                    ["baffle_name", "pos_x", "pos_y", "pos_z", "size_W", "size_H", "orientation_deg"])
    nf = write_csv("floor.csv", floors,
                    ["floor_name", "pos_y", "size_W", "size_D", "type", "n_patches"])
    nx = write_csv("box.csv", boxes,
                    ["box_name", "pos_x", "pos_y", "pos_z", "size_W", "size_D", "size_H", "orientation_deg"])

    print(f"  {case_name}: rack={nr} baffle={nb} floor={nf} box={nx}")


if __name__ == "__main__":
    cases = sorted(d.name for d in DEMO.iterdir()
                   if d.is_dir() and d.name.startswith("case_"))
    for cn in cases:
        extract_case(cn)
