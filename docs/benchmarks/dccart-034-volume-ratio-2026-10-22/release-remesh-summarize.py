"""Ten release cases re-meshed with the baffleCrumb build: the round's line, the strict gate, the mesh hash vs 2026-10-21."""
import glob, json, re
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "_tmp/release-remesh"
release = {c["case"]: c["meshReceipt"] for c in json.loads((REPO / "docs/benchmarks/air-release-cases-2026-10-21/summary.json").read_text())["cases"]}
rows = []
for case, receipt in release.items():
    log = sorted(glob.glob(str(OUT / case / "log/api/*/mesh-job.log")))[-1]
    text = Path(log).read_text()
    crumb = re.search(r"baffleCrumb .*?candidates (\d+), handed (\d+) to the solid; kept: skew (\d+)", text)
    mine = json.loads((OUT / case / "mesh-quality-receipt.json").read_text())
    rows.append({"case": case, "ranks": receipt["ranks"], "candidates": int(crumb.group(1)), "handed": int(crumb.group(2)),
                 "keptForSkew": int(crumb.group(3)), "status": mine["status"], "failedChecks": mine["failedChecks"],
                 "meshSha256": mine["meshSha256"], "release20261021Sha256": receipt["meshSha256"],
                 "sameMesh": mine["meshSha256"] == receipt["meshSha256"]})
for r in rows:
    print(f"{r['case']:16s} ranks {r['ranks']:2d}  candidates {r['candidates']} handed {r['handed']}  {r['status']} "
          f"({r['failedChecks']} failed)  same mesh as 10-21: {r['sameMesh']}")
(OUT / "summary.json").write_text(json.dumps(rows, indent=1) + "\n")
