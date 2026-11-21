"""Freeze the campaign records the generated appendix reads, into the tracked tree.

Why this exists.  `gen_appendix.tex` is generated from record JSONs that live in
gitignored `loggers/` directories and grow while a CFD campaign is running.  The
freshness gate compared the committed appendix against a regeneration over the
LIVE tree, so it was green at the commit instant and red 88 seconds later when the
next task record landed -- unsatisfiable while a campaign runs, and unfalsifiable
afterwards, because a reviewer who checks out the revision gets a red gate and a
PDF no rebuild reproduces.  Two round-4 reviews found this independently; it is
the same defect the ledger already carries three rows for, arriving by a fourth
route.

It also means the evidence behind every number in the appendix now ships with the
paper instead of travelling by rsync.

Refreshing the snapshot is this script, run explicitly, and the diff is reviewable:
`make snapshot && make archive`.
"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
DEST = HERE / "records" / "campaign_snapshot"

PATTERNS = [
    "ddl/case/benchB/E11_cross_fidelity/loggers/e11_pilot/pilot_numbers.json",
    "ddl/case/benchB/E11b_surrogate_search/loggers/derive/review_numbers.json",
    "ddl/case/benchB/E11b_surrogate_search/loggers/e11b_full/results_e11b.json",
    "ddl/case/benchB/E11d_bo_baseline/loggers/*/results_e11d_reeval.json",
    "ddl/case/benchB/E11d_bo_baseline/loggers/*/task_*.json",
    "ddl/case/benchB/E11e_multiroom/loggers/*/task_*.json",
    "ddl/case/benchB/E11f_adjoint_ablation/loggers/*/results_e11f.json",
    "ddl/case/benchB/E11g_mesh_fidelity/loggers/*/results_e11g.json",
    "ddl/case/benchC/E12c_policy_eval/loggers/*/results_e12c.json",
]


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "repo_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                            capture_output=True, text=True).stdout.strip(),
                "patterns": {}}
    total = 0
    for pat in PATTERNS:
        files = sorted(REPO.glob(pat))
        entries = []
        for f in files:
            rel = str(f.relative_to(REPO)).replace("/", "__")
            blob = f.read_bytes()
            (DEST / rel).write_bytes(blob)
            entries.append([rel, hashlib.sha256(blob).hexdigest(), len(blob)])
        manifest["patterns"][pat] = entries
        total += len(entries)
        print(f"  {len(entries):3d}  {pat}")
    keep = {e[0] for v in manifest["patterns"].values() for e in v} | {"manifest.json"}
    removed = [p.name for p in DEST.iterdir() if p.name not in keep]
    for p in DEST.iterdir():
        if p.name not in keep:
            p.unlink()
    manifest["n_files"] = total
    manifest["n_removed_stale"] = len(removed)
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"snapshot: {total} files into {DEST.relative_to(REPO)}"
          + (f", removed {len(removed)} stale: {removed}" if removed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
