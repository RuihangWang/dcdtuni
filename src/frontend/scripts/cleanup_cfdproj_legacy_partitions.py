"""Clean legacy auto-generated u-slot partitions from .cfdproj files.

Context
-------
Before commit 666e7d3 (2026-04-14, "remove auto-partition logic"), newly
created racks got auto-generated partition boards whenever blanking plates
were placed. That behavior was removed and is now fully manual: partitions
default to `deleted: true` and only appear when the user explicitly clicks
"Add partition" in the UI.

Older .cfdproj files saved before the fix still carry these legacy
auto-generated partitions as `deleted: false`. Since cloneRackState
preserves the saved flag on load, those partitions keep showing up in the
3D viewport and in STL export.

Rule
----
For each partition at index i (between u-slot i and u-slot i+1), if **both**
adjacent u-slots have their blanking plates fully intact on BOTH sides
(front + back = 4 panels total), the partition is considered legacy auto-
generated junk and is set to `deleted: true`. If ANY of the 4 adjacent
panels was manually deleted by the user, the partition is preserved as-is
(the user may have added the partition intentionally to divide an open
through-zone).

Usage
-----
    # Dry run on one file (report only, no write):
    python scripts/cleanup_cfdproj_legacy_partitions.py projects/Foo.cfdproj --dry-run

    # Actually clean one file (writes .cfdproj.bak backup):
    python scripts/cleanup_cfdproj_legacy_partitions.py projects/Foo.cfdproj

    # Clean all projects under projects/ (recursive search):
    python scripts/cleanup_cfdproj_legacy_partitions.py projects/ --dry-run
    python scripts/cleanup_cfdproj_legacy_partitions.py projects/
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _is_deleted(arr: list, i: int) -> bool:
    """Read deletedFaces.front[i] or .back[i] tolerantly.

    Arrays may be shorter than unitCount (JS sparse-ish representation) or
    may have undefined entries represented as None; treat missing as
    not-deleted (blanking plate intact).
    """
    if not isinstance(arr, list) or i < 0 or i >= len(arr):
        return False
    return bool(arr[i])


def clean_rack_partitions(rack: dict) -> tuple[int, int]:
    """Mutate rack['partitions'] in place. Returns (cleaned_count, preserved_count).

    For each partition with deleted=False, re-evaluate against the
    "both adjacent u-slots fully intact" rule. Partitions that were already
    deleted=True are left alone.
    """
    partitions = rack.get("partitions")
    if not isinstance(partitions, list):
        return (0, 0)
    df = rack.get("deletedFaces") or {}
    front = df.get("front") or []
    back = df.get("back") or []

    cleaned = 0
    preserved_with_edit_nearby = 0
    for p in partitions:
        # Skip already-deleted partitions (nothing to do)
        if p.get("deleted", True):
            continue
        idx = p.get("index")
        if not isinstance(idx, int) or idx < 0:
            continue
        # Partition idx sits between u-slot idx and u-slot idx+1
        any_deleted = (
            _is_deleted(front, idx)     or _is_deleted(back, idx)
            or _is_deleted(front, idx + 1) or _is_deleted(back, idx + 1)
        )
        if any_deleted:
            preserved_with_edit_nearby += 1
            continue
        # All 4 adjacent blanking panels intact → legacy junk, delete it
        p["deleted"] = True
        cleaned += 1
    return cleaned, preserved_with_edit_nearby


def clean_project_file(path: Path, dry_run: bool) -> dict:
    """Load project, clean every rack, optionally write back.

    Returns a report dict with counts.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    models = data.get("models")
    if not isinstance(models, list):
        return {"path": str(path), "error": "no 'models' array"}

    total_cleaned = 0
    total_preserved = 0
    per_rack = []
    for m in models:
        if m.get("kind") != "rack":
            continue
        rack = m.get("rack")
        if not isinstance(rack, dict):
            continue
        cleaned, preserved = clean_rack_partitions(rack)
        total_cleaned += cleaned
        total_preserved += preserved
        if cleaned or preserved:
            per_rack.append({
                "name": m.get("name"),
                "cleaned": cleaned,
                "preserved_near_delete": preserved,
            })

    report = {
        "path": str(path),
        "total_cleaned": total_cleaned,
        "total_preserved_near_delete": total_preserved,
        "per_rack": per_rack,
        "dry_run": dry_run,
    }

    if not dry_run and total_cleaned > 0:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        report["backup"] = str(bak)
        report["written"] = True
    else:
        report["written"] = False

    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target",
                    help="a single .cfdproj file OR a directory to scan recursively")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not write")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        sys.exit(2)

    if target.is_file():
        files = [target]
    else:
        files = sorted(target.rglob("*.cfdproj"))

    if not files:
        print(f"no .cfdproj files under {target}")
        sys.exit(0)

    grand_cleaned = 0
    grand_preserved = 0
    for f in files:
        report = clean_project_file(f, args.dry_run)
        if "error" in report:
            print(f"[SKIP] {f}: {report['error']}")
            continue
        grand_cleaned += report["total_cleaned"]
        grand_preserved += report["total_preserved_near_delete"]
        if report["total_cleaned"] == 0 and report["total_preserved_near_delete"] == 0:
            print(f"[OK  ] {f}: no visible partitions to evaluate")
            continue
        verb = "would clean" if args.dry_run else "cleaned"
        suffix = f" (backup: {report.get('backup', 'n/a')})" if report.get("written") else ""
        print(f"[{'DRY ' if args.dry_run else 'DONE'}] {f}: {verb} {report['total_cleaned']}, "
              f"preserved-near-delete {report['total_preserved_near_delete']}{suffix}")
        for r in report["per_rack"]:
            print(f"         - {r['name']}: cleaned={r['cleaned']} preserved={r['preserved_near_delete']}")

    print()
    print(f"=== SUMMARY: {'would clean' if args.dry_run else 'cleaned'} "
          f"{grand_cleaned} partitions, preserved {grand_preserved} near edits, "
          f"across {len(files)} file(s) ===")


if __name__ == "__main__":
    main()
