#!/usr/bin/env python3
"""Batch-migrate .cfdproj files to schemaVersion 4.

Usage: python migrate_cfdproj_to_v4.py <dir-or-file>... [--write]
Dry-run by default: prints files that WOULD change. --write rewrites in place
(2-space indent, matching the editor's serializeProjectState output).
NOTE: tracked projects are converted only in Plan 4, after the acceptance gate.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfdproj_migrate import migrate_cfdproj_to_v4  # noqa: E402


def run(argv):
    write = "--write" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    targets = []
    for p in paths:
        targets.extend(sorted(p.rglob("*.cfdproj")) if p.is_dir() else [p])
    changed = []
    for f in targets:
        raw = json.loads(f.read_text(encoding="utf-8"))
        v4 = migrate_cfdproj_to_v4(raw)
        if v4 == raw:
            continue
        changed.append(str(f))
        if write:
            f.write_text(json.dumps(v4, indent=2, ensure_ascii=False), encoding="utf-8")
        print(("MIGRATED " if write else "WOULD MIGRATE ") + str(f))
    return changed


if __name__ == "__main__":
    run(sys.argv[1:])
