#!/usr/bin/env python3
"""Verify preserved native two-rank fields/times without executing CFD."""
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEOMETRY = ROOT.parent / "steady-start-lifecycle/accepted-current-simple-nonzero-mpi2"
PARTITIONS = ("processor0", "processor1")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def internal(text):
    match = re.search(r"\binternalField\s+(.*?)\n\s*boundaryField\b", text, re.S)
    assert match, "missing internal field"
    return match.group(1).strip()


def scalars(statement):
    match = re.fullmatch(r"uniform\s+(\S+);", statement)
    if match:
        return [float(match.group(1))]
    match = re.fullmatch(r"nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;", statement, re.S)
    assert match, "not a scalar field"
    values = [float(x) for x in match.group(2).split()]
    assert len(values) == int(match.group(1))
    return values


def location0(text):
    return re.sub(r'(\blocation\s+)"[^"]*"', r'\1"0"', text, count=1)


def boundary_values(text, patch):
    match = re.search(r"\b" + re.escape(patch) + r"\s*\{", text)
    assert match, patch
    start = match.end()
    depth = 1
    cursor = start
    while depth:
        if text[cursor] == "{":
            depth += 1
        elif text[cursor] == "}":
            depth -= 1
        cursor += 1
    block = text[start:cursor - 1]
    value = re.search(r"\bvalue\s+((?:uniform|nonuniform).*?;)", block, re.S)
    assert value, patch
    return scalars(value.group(1))


def verify(name, expected_times, end, event_fields=False):
    folder = ROOT / ("after-" + name)
    run = folder / "run"
    status = json.loads((folder / "status.json").read_text())
    assert status["done"] and status["returncode"] == 0
    assert json.loads((folder / "export.json").read_text())["success"]
    manifest = json.loads((folder / "input.cfdrun").read_text())
    assert manifest["simulationConfig"]["endTimeSeconds"] == end
    assert manifest["transient"]["durationS"] == end
    report = json.loads((run / "steady_start.json").read_text())
    assert report["actual_n_procs"] == 2
    assert report["convergence"]["converged_iteration"] == 337
    assert report["convergence"]["verdict"] == "STEADY_CONVERGED"
    assert report["mesh_layout_sha256"] == "39f2dba3122b1a9bd6974ec8bc2997ee2a9c2c0c817e74dca0e22cea7d74f7dd"
    log = (run / "log.mpi_solver").read_text()
    times = [float(t) for t in re.findall(r"^Time = (.+)$", log, re.M)]
    assert times == expected_times, (name, times)
    assert re.search(r"^End\s*$", log, re.M)
    assert "Solving for T" in log and "Solving for p_rgh" in log
    assert "FOAM FATAL" not in log
    dt = [times[0], *[b - a for a, b in zip(times, times[1:])]]
    assert min(dt) > 0
    assert max(dt) <= manifest["simulationConfig"]["deltaT"] * (1 + 1e-12)
    assert max(times) == end
    verdict = json.loads((run / "convergence.json").read_text())
    assert verdict["verdict"] == "TRANSIENT_COMPLETED"
    assert verdict["transient"]["outer_loop"]["steps_hit_limit"] == 0
    receipt = json.loads((run / "air-run-execution-receipt.json").read_text())
    assert receipt["status"] == "accepted"
    result = receipt["result"]
    assert result["cfdrunSha256"] == sha(folder / "input.cfdrun")
    assert result["timeValue"] == end
    assert result["meshSha256"] == "eae64d9d07933f514f191f45c12455cd1ffcc788ff619fb5d934f12fc580d530"
    for item in result["meshFiles"]:
        assert sha(GEOMETRY / item["path"]) == item["sha256"], item["path"]
    for item in result["fieldFiles"]:
        assert sha(run / item["path"]) == item["sha256"], item["path"]
    phi = []
    for rank in PARTITIONS:
        initial = run / rank / "0"
        steady = run / "steadyStart" / rank / "337"
        assert location0((initial / "phi").read_text()) == location0((steady / "phi").read_text())
        phi += scalars(internal((initial / "phi").read_text()))
        for field in ("T", "U", "alphat", "epsilon", "k", "nut", "p", "p_rgh"):
            assert internal((initial / field).read_text()) == internal((steady / field).read_text())
        for instant in (run / rank).iterdir():
            if not instant.is_dir():
                continue
            for field in instant.iterdir():
                if not field.is_file():
                    continue
                text = field.read_text()
                assert not re.search(r"(?i)(?<!\w)[+-]?(?:nan|inf(?:inity)?)(?!\w)", text)
                numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", internal(text))
                assert all(math.isfinite(float(v)) for v in numbers)
    assert max(abs(v) for v in phi) > 0
    summary = {"jobId": status["jobId"], "verified": True, "mpiRanks": 2,
               "globalCells": 511, "steadyIteration": 337, "timesS": times,
               "terminalFieldTimeS": result["timeValue"], "wholePhiSeedExact": True,
               "nonzeroSeedPhi": True, "maxDeltaTActualS": max(dt)}
    if event_fields:
        patch = "Channel_supply_Back_patch"
        old_values = []
        new_values = []
        for rank in PARTITIONS:
            old_values += boundary_values((run / rank / "0/T").read_text(), patch)
            new_values += boundary_values((run / rank / "0.003/T").read_text(), patch)
        assert all(x == 295.15 for x in old_values)
        assert len(new_values) == 64 and all(x == 297.15 for x in new_values)
        summary["eventTimeS"] = .003
        summary["eventSupplyFaces"] = len(new_values)
        summary["eventSupplyTemperatureK"] = 297.15
    return summary


def audit():
    before = ROOT / "before-dt-over-end"
    assert json.loads((before / "status.json").read_text())["returncode"] == 1
    log = (before / "run/log.mpi_solver").read_text()
    assert not re.search(r"^Time = ", log, re.M)
    assert "Starting time loop" in log and re.search(r"^End\s*$", log, re.M)
    assert "SIMPLE solution converged in 337 iterations" in (before / "run/log.steady_start").read_text()
    sources = json.loads((ROOT / "build/source-all-sha256.json").read_text())
    for path, expected in sources.items():
        assert sha(ROOT / "build/source" / path) == expected, path
    installation = json.loads((ROOT / "build/install.json").read_text())
    assert installation["installedSha256"] == "b49ea3b9140e2943d0712bf021c0a9c54d40d17be64c297c4938e753903d4e3b"
    assert all(c["returncode"] == 1 and not c["stdout"].strip() for c in installation["fuserChecks"])
    quality = json.loads((ROOT / "strict-geometry/latest-input-quality-receipt.json").read_text())
    assert quality["status"] == "accepted" and quality["failedChecks"] == 0
    assert quality["ranks"] == 2
    assert quality["cfdrunSha256"] == sha(ROOT / "after-breakpoint-fields/input.cfdrun")
    raw = (ROOT / "strict-geometry/same-geometry-full-check.sse.log").read_text()
    assert "checkMesh -allTopology -allGeometry -parallel" in raw
    assert "Mesh OK." in raw and "accepted (0 failed checks)" in raw
    results = [
        verify("dt-over-end", [.0012, .002, .003, .004, .005], .005),
        verify("nondivisible", [.01, .02, .026], .026),
        verify("breakpoint", [.003, .006, .009, .011], .011),
        verify("breakpoint-fields", [.003, .006, .009, .011], .011, event_fields=True),
    ]
    return {"sourceCommit": installation["sourceCommit"],
            "pimpleElfSha256": installation["installedSha256"],
            "oldPimpleZeroStepReproduced": True, "cases": results}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
