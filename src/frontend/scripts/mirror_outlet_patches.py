#!/usr/bin/env python3
"""One-off migration: mirror all server outlet patches to the right edge.

After the Y-up coordinate refactor, every rack's server outlet patch
ended up flush against the left edge (x=0) of its face. The physical
intent is for outlet openings to sit on the opposite side. This script
flips each rectangular/circular outlet patch along the face's x-axis in
normalized [0, 1] space.

Scope:
  - Only touches `models[i].rack.equipments[j].faces.outlet.patches`.
  - Leaves inlet/top/bottom/left/right faces untouched.
  - Leaves non-rack models untouched.
  - Running twice is a no-op (mirror is an involution).

Usage:
    python mirror_outlet_patches.py --dry-run  <path-or-dir>
    python mirror_outlet_patches.py --backup   <path-or-dir>
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _mirror_patch(patch: dict) -> None:
    if patch.get("type") == "circle":
        patch["cx"] = 1.0 - patch.get("cx", 0)
    else:
        w = patch.get("w", 0)
        patch["x"] = 1.0 - patch.get("x", 0) - w


def mirror_outlet_patches(model: dict) -> int:
    """Mirror outlet patches in a single model. Returns count touched."""
    if model.get("kind") != "rack":
        return 0
    rack = model.get("rack") or {}
    changed = 0
    for eq in rack.get("equipments") or []:
        face = (eq.get("faces") or {}).get("outlet") or {}
        for patch in face.get("patches") or []:
            _mirror_patch(patch)
            changed += 1
    return changed


def mirror_outlet_patches_in_project(proj: dict) -> int:
    total = 0
    for model in proj.get("models") or []:
        total += mirror_outlet_patches(model)
    return total


def migrate_file(path: Path, dry_run: bool, backup: bool) -> int:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    changed = mirror_outlet_patches_in_project(data)
    if changed == 0:
        print(f"  (skip) {path.name}: no outlet patches")
        return 0
    output = json.dumps(data, ensure_ascii=False, indent=2)

    if dry_run:
        print(f"[dry-run] {path.name}: would mirror {changed} outlet patch(es)")
        return changed

    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    path.write_text(output + "\n", encoding="utf-8")
    print(f"  migrated {path.name}: mirrored {changed} outlet patch(es)")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path, help="file, or directory of *.cfdproj")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--pattern", default="*.cfdproj",
                    help="glob when target is a directory")
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
    total = 0
    for path in targets:
        total += migrate_file(path, dry_run=args.dry_run, backup=args.backup)
    print(f"{'would mirror' if args.dry_run else 'mirrored'} {total} outlet "
          f"patch(es) across {len(targets)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
