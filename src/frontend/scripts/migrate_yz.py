#!/usr/bin/env python3
"""One-off migration: swap y↔z in cfdproj/cfdrun files for the Y-up refactor.

Strategy: recursively walk the JSON tree. For any dict, detect its shape and
apply the axis-aware swap that preserves physical geometry:

  - 3D point / vector with {x, y, z}  → swap y↔z values
  - Quaternion with {x, y, z, w}      → (x, y, z, w) → (x, z, y, -w)
      (geometry swap y↔z is a basis reflection P. The rotation that preserves
      visual orientation under this basis change is R_new = P·R_old·P. For a
      quaternion (x,y,z,w), this is equivalent to (x, z, y, -w). A naive
      component swap (x,z,y,w) gives the INVERSE rotation — silently wrong.)
  - Bounds dict with {yMin,yMax,zMin,zMax} → swap paired values
  - cfdRoom named gravity pair {gravityY, gravityZ} → swap values

Dicts with only {x, y} (2D plan points, face-local UVs) are LEFT ALONE.
Arrays of coordinate pairs like [[x,y], ...] are lists, not dicts, so the
rules never fire.

Usage:
    python migrate_yz.py --dry-run  <path-or-dir>
    python migrate_yz.py --backup   <path-or-dir>
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def swap_yz(obj):
    """Recursively walk obj, swapping y and z in 3D dicts in-place.
    Quaternions get (x, z, y, -w); vectors get (x, z, y)."""
    if isinstance(obj, dict):
        # Recurse children first so nested swaps happen before the parent.
        for v in obj.values():
            swap_yz(v)

        keys = obj.keys()

        # Bounds dict
        if "yMin" in keys and "zMin" in keys:
            obj["yMin"], obj["zMin"] = obj["zMin"], obj["yMin"]
        if "yMax" in keys and "zMax" in keys:
            obj["yMax"], obj["zMax"] = obj["zMax"], obj["yMax"]

        # Named gravity pair in cfdRoom
        if "gravityY" in keys and "gravityZ" in keys:
            obj["gravityY"], obj["gravityZ"] = obj["gravityZ"], obj["gravityY"]

        # Quaternion {x, y, z, w} — must negate w when swapping y↔z to preserve
        # rotation direction under basis reflection.
        if "y" in keys and "z" in keys and "w" in keys and "x" in keys:
            obj["y"], obj["z"] = obj["z"], obj["y"]
            obj["w"] = -obj["w"]
        # Plain 3D dict {x, y, z} (vector / scale / moveTool flags)
        elif "y" in keys and "z" in keys:
            obj["y"], obj["z"] = obj["z"], obj["y"]
    elif isinstance(obj, list):
        for item in obj:
            swap_yz(item)


def _flip_rect(patch, axis: str, dim: float) -> None:
    """In-place flip lower-left corner of a rect patch.

    axis='x': x → dim - x - w  (for front/back/left/right faces)
    axis='y': y → dim - y - h  (for top/bottom faces)
    """
    if axis == "x":
        w = patch.get("w", 0)
        patch["x"] = dim - patch["x"] - w
    else:
        h = patch.get("h", 0)
        patch["y"] = dim - patch["y"] - h


def _flip_circle(patch, axis: str, dim: float) -> None:
    key = "cx" if axis == "x" else "cy"
    patch[key] = dim - patch[key]


# Axis to flip per face; dimension selector: 'W' or 'D' from model.base.
_FACE_FLIP = {
    "front":  ("x", "W"),
    "back":   ("x", "W"),
    "left":   ("x", "D"),
    "right":  ("x", "D"),
    "top":    ("y", "D"),
    "bottom": ("y", "D"),
}
# Normalized (server equipment) variant — dim = 1 everywhere.
_SERVER_FACE_ALIASES = {"inlet": "front", "outlet": "back"}


def _flip_patch(patch, axis: str, dim: float) -> None:
    if patch.get("type") == "circle":
        _flip_circle(patch, axis, dim)
    else:
        _flip_rect(patch, axis, dim)


def _flip_polygon_rings(polygon, axis: str, dim: float) -> None:
    """Flip coordinate along the given axis in place.

    Accepts either MultiPolygon[Polygon[Ring[[x, y]]]] or the shorter
    Polygon[Ring[[x, y]]] form — real cfdproj files use both. We descend
    recursively and flip any 2-element numeric leaf (a point).
    """
    idx = 0 if axis == "x" else 1

    def walk(node):
        if not isinstance(node, list):
            return
        if len(node) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in node):
            node[idx] = dim - node[idx]
            return
        for sub in node:
            walk(sub)

    walk(polygon)


def migrate_patches(model: dict) -> None:
    """Flip face-local patches/cutouts/polygons for Y-up consistency.

    Model types covered: box/wall/baffle/floor/partitionWall/acu/cdu/crh and
    any other kind that stores patches under `model.faces[face_id].patches`.
    Racks additionally have per-equipment patches under
    `model.rack.equipments[i].faces[face_id].patches` in normalized [0,1] space.
    """
    base = model.get("base") or {}
    W = base.get("W", 0) or 0
    D = base.get("D", 0) or 0
    dims = {"W": W, "D": D}

    # Box-style patches under model.faces[face_id].patches
    for face_id, face in (model.get("faces") or {}).items():
        if not isinstance(face, dict):
            continue
        spec = _FACE_FLIP.get(face_id)
        if not spec:
            continue
        axis, dim_key = spec
        dim = dims[dim_key]
        for patch in face.get("patches") or []:
            _flip_patch(patch, axis, dim)

    # Cutouts on a face: model.cutouts = [{faceId, polygon, ...}]
    for cutout in model.get("cutouts") or []:
        face_id = cutout.get("faceId")
        spec = _FACE_FLIP.get(face_id)
        if not spec:
            continue
        axis, dim_key = spec
        poly = cutout.get("polygon")
        if poly:
            _flip_polygon_rings(poly, axis, dims[dim_key])

    # Floor 'model.polygon' is face-local on the 'top' face (y along world -Z
    # after Y-up layout change). Flip y using D.
    if model.get("kind") == "floor" and isinstance(model.get("polygon"), list):
        poly = model["polygon"]
        for pt in poly:
            if len(pt) >= 2:
                pt[1] = D - pt[1]

    # Rack equipment patches: normalized [0, 1] face coords — dim = 1.
    for eq in ((model.get("rack") or {}).get("equipments") or []):
        for face_id, face in (eq.get("faces") or {}).items():
            if not isinstance(face, dict):
                continue
            canonical_face = _SERVER_FACE_ALIASES.get(face_id, face_id)
            spec = _FACE_FLIP.get(canonical_face)
            if not spec:
                continue
            axis, _ = spec
            for patch in face.get("patches") or []:
                _flip_patch(patch, axis, 1.0)


def migrate_patches_in_project(proj: dict) -> None:
    for model in proj.get("models") or []:
        migrate_patches(model)


def migrate_file(path: Path, dry_run: bool, backup: bool, patches_only: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if patches_only:
        migrate_patches_in_project(data)
    else:
        swap_yz(data)
    output = json.dumps(data, ensure_ascii=False, indent=2)

    if dry_run:
        before = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        changed_lines = sum(1 for a, b in zip(before.split("\n"), output.split("\n")) if a != b)
        print(f"[dry-run] {path.name}: {changed_lines} lines would change")
        return

    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    path.write_text(output + "\n", encoding="utf-8")
    print(f"  migrated {path.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path, help="file, dir of *.cfdproj, or dir of *.cfdrun")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--patches-only", action="store_true",
                    help="Only flip face-local patch / cutout / polygon coords; "
                         "do NOT re-swap 3D points. Use to fix already-migrated "
                         "cfdproj files whose patches didn't get flipped.")
    ap.add_argument("--pattern", default="*.cfdproj", help="glob when target is a directory")
    args = ap.parse_args()

    if args.target.is_file():
        targets = [args.target]
    elif args.target.is_dir():
        targets = sorted(args.target.rglob(args.pattern))
    else:
        print(f"error: {args.target} is neither file nor dir", file=sys.stderr)
        return 2

    if not targets:
        print(f"no files matching {args.pattern} under {args.target}", file=sys.stderr)
        return 1

    print(f"{'DRY RUN' if args.dry_run else 'MIGRATING'} {len(targets)} files "
          f"({'no backup' if not args.backup else 'with .bak'})")
    for path in targets:
        migrate_file(path, dry_run=args.dry_run, backup=args.backup,
                     patches_only=args.patches_only)

    return 0


if __name__ == "__main__":
    sys.exit(main())
