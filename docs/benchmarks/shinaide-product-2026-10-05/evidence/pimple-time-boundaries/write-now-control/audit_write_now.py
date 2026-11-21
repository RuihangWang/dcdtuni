#!/usr/bin/env python3
"""Read-only verification of one real native writeNow stop."""
import datetime
import hashlib
import json
import math
import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(ROOT.parent / "audit_time_boundaries.py"))
sha = HELPERS["sha"]
internal = HELPERS["internal"]
scalars = HELPERS["scalars"]
location0 = HELPERS["location0"]
geometry = HELPERS["GEOMETRY"]

request = json.loads((ROOT / "stop-request.json").read_text())
status = json.loads((ROOT / "status.json").read_text())
response = json.loads((ROOT / "api-convergence-response.json").read_text())
assert response["ok"]
verdict = json.loads(response["content"])
assert verdict == json.loads((ROOT / "run/convergence.json").read_text())
assert status["done"] and status["returncode"] == 0
assert verdict["verdict"] == "TRANSIENT_INCOMPLETE"
assert verdict["transient"]["passed"] is False
assert verdict["transient"]["end_time"] == .5
assert not request["statusBeforeRequest"]["done"]
before = (ROOT / "controlDict-before").read_text()
changed = (ROOT / "controlDict-request").read_text()
assert re.sub(r"(stopAt\s+)endTime;", r"\1writeNow;", before, count=1) == changed
assert changed == (ROOT / "controlDict-final").read_text()
assert re.search(r"endTime\s+0.5;", changed)
assert request["afterMtimeNs"] > request["beforeMtimeNs"] + 6_000_000_000
utc = datetime.datetime.fromisoformat(request["utc"]).timestamp()
assert abs(request["afterMtimeNs"] / 1e9 - utc) < 1
active = (ROOT / "native-at-request.log").read_text()
assert "buoyantBoussinesqPimpleFoam_new -parallel" in active and "Solving for T" in active
log = (ROOT / "run/log.mpi_solver").read_text()
times = [float(x) for x in re.findall(r"^Time = ([0-9.eE+-]+)$", log, re.M)]
assert len(times) == 127
assert all(a < b for a, b in zip(times, times[1:]))
assert request["lastObservedPhysicalTimeS"] == .123
assert times[-1] == .127 == verdict["transient"]["final_time"]
assert "Re-reading object controlDict" in log
assert re.search(r"^End\s*$", log, re.M)
assert "FOAM FATAL" not in log
assert all(t < .5 for t in times)
sse = (ROOT / "job.sse.log").read_text()
assert "convergence: TRANSIENT_INCOMPLETE" in sse
assert "convergence: TRANSIENT_COMPLETED" not in sse
identity = json.loads((ROOT / "source-binary-identity.json").read_text())
parent_sources = json.loads((ROOT.parent / "build/source-all-sha256.json").read_text())
assert identity["sourceSha256"] == parent_sources
for path, expected in parent_sources.items():
    assert sha(ROOT.parent / "build/source" / path) == expected
for name, item in identity["executables"].items():
    assert item["fuserReturncode"] == 1 and not item["fuserStdout"].strip()
assert request["pimpleSha256Before"] == identity["executables"]["buoyantBoussinesqPimpleFoam_new"]["sha256"]
assert request["simpleSha256Before"] == identity["executables"]["buoyantBoussinesqSimpleFoam_new"]["sha256"]
execution = json.loads((ROOT / "run/air-run-execution-receipt.json").read_text())
assert execution["status"] == "accepted"
result = execution["result"]
assert result["timeValue"] == .127
assert result["cfdrunSha256"] == sha(ROOT / "input.cfdrun")
for item in result["fieldFiles"]:
    assert sha(ROOT / "run" / item["path"]) == item["sha256"]
for item in result["meshFiles"]:
    assert sha(geometry / item["path"]) == item["sha256"]
quality = json.loads((ROOT / "input-quality-receipt.json").read_text())
assert quality["status"] == "accepted" and quality["failedChecks"] == 0
assert quality["cfdrunSha256"] == result["cfdrunSha256"]
assert quality["meshSha256"] == result["meshSha256"]
steady = json.loads((ROOT / "run/steady_start.json").read_text())
assert steady["actual_n_procs"] == 2 and steady["convergence"]["converged_iteration"] == 337
phi = []
for rank in ("processor0", "processor1"):
    seed = ROOT / "run" / rank / "0"
    initialized = ROOT / "run/steadyStart" / rank / "337"
    assert location0((seed / "phi").read_text()) == location0((initialized / "phi").read_text())
    phi += scalars(internal((seed / "phi").read_text()))
    for field in (ROOT / "run" / rank / "0.127").iterdir():
        if not field.is_file():
            continue
        text = field.read_text()
        assert not re.search(r"(?i)(?<!\w)[+-]?(?:nan|inf(?:inity)?)(?!\w)", text)
        numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", internal(text))
        assert all(math.isfinite(float(x)) for x in numbers)
assert max(abs(v) for v in phi) > 0
print(json.dumps({"verified": True, "jobId": status["jobId"], "mpiRanks": 2,
                  "requestedEndTimeS": .5, "stopRequestObservedTimeS": .123,
                  "genuineFinalFieldTimeS": .127, "computedTimeSteps": len(times),
                  "apiVerdict": verdict["verdict"], "mpiNormalExitCode": status["returncode"],
                  "controlDictReloadProven": True, "noRevivalToEndTime": True,
                  "sourceAndElfUnchanged": True, "wholeNonzeroPhiSeedExact": True}, indent=2))
