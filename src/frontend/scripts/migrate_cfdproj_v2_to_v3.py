"""Batch-convert .cfdproj files v2/legacy -> v3 in place. Idempotent.

Usage:
    python scripts/migrate_cfdproj_v2_to_v3.py case/ docs/manuscript/paper_experiment/ --dry-run
    python scripts/migrate_cfdproj_v2_to_v3.py case/ docs/manuscript/paper_experiment/
"""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfdproj_migrate import migrate_v2_to_v3, SCHEMA_VERSION

SKIP_DIRS = {"Slab_dataset"}


def iter_files(targets):
    for t in targets:
        p = Path(t)
        if p.is_file() and p.suffix == ".cfdproj":
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.cfdproj")):
                if any(part in SKIP_DIRS for part in f.parts):
                    continue
                yield f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="files or directories")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    converted = skipped = failed = 0
    for f in iter_files(args.targets):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP (bad file): {f}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if int(raw.get("schemaVersion") or 0) >= SCHEMA_VERSION:
            skipped += 1
            continue
        v3 = migrate_v2_to_v3(raw)
        print(f"{'DRY ' if args.dry_run else ''}convert: {f}  (models={len(raw.get('models') or [])})")
        if not args.dry_run:
            if not args.no_backup:
                shutil.copy2(f, f.with_suffix(".cfdproj.bak"))
            f.write_text(json.dumps(v3, ensure_ascii=False, indent=2), encoding="utf-8")
        converted += 1
    print(f"\n{'would convert' if args.dry_run else 'converted'}: {converted}, already v3: {skipped}"
          + (f", failed: {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
