"""Collect the return-air series test_convergence_run_length.py replays.

Run from the repository root once the runs exist locally (case/*/log and
_tmp are git-ignored):

    python src/backend/tests/fixtures/return_air_replay/make_series.py \\
        src/backend/tests/fixtures/return_air_replay/series.json.gz

Reads, for each run, the configured ACU return monitors named in its
convergence.json (``sensors.streams.acuReturnPatches.verdict.monitors`` or
``sensors.monitors``) and the solver log's iteration count, and writes one JSON
document with each monitor's values and the SHA-256 of the file they came from.
"""
import gzip, hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "src"))
from backend.aircooling.runner.convergence import parse_solver_log, _read_return_patch_series  # noqa: E402

RELEASE = ["Base2-9/log/2026-10-20-18-11-06_Base2-9", "Base604/log/2026-10-20-21-33-20_Base604",
           "Base_leakage/log/2026-10-20-17-17-30_Base_leakage", "CMI-China/log/2026-10-21-04-39-56_CMI-China",
           "DAML-Testbed/log/2026-10-20-19-54-07_DAML-Testbed", "Shinaide/log/2026-10-21-04-39-58_Shinaide",
           "Shinaide_Liquid/log/2026-10-20-17-03-35_Shinaide_Liquid", "Slab/log/2026-10-20-19-25-05_Slab",
           "STDCT/log/2026-10-20-20-14-40_STDCT", "Tri_angle/log/2026-10-20-18-13-06_Tri_angle"]
SHORT = ["_tmp/campaign_B5verify/cases/SingleRack_doc/log/2026-10-22-02-56-48_SingleRack_doc",
         "_tmp/campaign_B5verify/cases/Shinaide_Liquid/log/2026-10-22-03-03-42_Shinaide_Liquid",
         "_tmp/campaign_B5verify/cases/SingleRack_bA/log/2026-10-22-03-10-50_SingleRack_bA"]


def collect(run: Path, group: str):
    report = json.loads((run / "convergence.json").read_text())
    sensors = report["sensors"]
    returns = (sensors.get("streams") or {}).get("acuReturnPatches", {}).get("verdict") or sensors
    log = run / Path(report["log"]).name
    iterations = len(parse_solver_log(log).iterations)
    monitors = []
    for row in returns["monitors"]:
        path = run / "postProcessing" / row["functionName"] / Path(row["path"]).parent.name / Path(row["path"]).name
        times, values, source = _read_return_patch_series(path, run, row["patch"])
        assert source["sha256"] == row["sha256"], path
        assert len(values) == iterations, (path, len(values), iterations)
        # areaAverage(T) is written with 7 significant digits: 1e-4 K steps,
        # stored as the first value and integer deltas in those steps
        ticks = [round(v * 10000) for v in values]
        assert all(t / 10000 == v for t, v in zip(ticks, values)), path
        monitors.append({"patch": row["patch"], "sha256": source["sha256"], "first": ticks[0],
                         "deltas": [b - a for a, b in zip(ticks, ticks[1:])]})
    return {"group": group, "run": str(run.relative_to(REPO)), "convergenceJsonSha256":
            hashlib.sha256((run / "convergence.json").read_bytes()).hexdigest(),
            "recordedVerdict": report["verdict"], "iterations": iterations,
            "logSha256": hashlib.sha256(log.read_bytes()).hexdigest(), "monitors": monitors}


runs = [collect(REPO / "case" / r, "release") for r in RELEASE] + [collect(REPO / r, "short") for r in SHORT]
doc = {"format": "dcdtuni.test.return-air-replay/v1", "unitK": 1e-4,
       "source": "configured ACU return areaAverage(T) monitors of each run (postProcessing/avgT_<patch>/0/surfaceFieldValue.dat)",
       "runs": runs}
out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
data = json.dumps(doc, separators=(",", ":")).encode()
with gzip.GzipFile(out, "wb", mtime=0) as fh:
    fh.write(data)
print(out, len(data), out.stat().st_size)
for r in runs:
    print(r["run"], r["recordedVerdict"], r["iterations"], len(r["monitors"]))
