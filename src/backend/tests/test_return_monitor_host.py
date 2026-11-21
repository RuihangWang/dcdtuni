"""A configured ACU return monitor is judged by its binding, not by the
collection that hosts the grille: DAML-Testbed returns air through ceiling
grilles (constructions.floors) and a return box (constructions.boxes)."""
import hashlib
import json
from pathlib import Path

from backend.aircooling.runner.convergence import configured_return_patch_verdict

N = 40


def _case(tmp_path: Path, object_path: str, role: str = "acu_return") -> Path:
    case = tmp_path / "case"
    run = case / "log" / "run"
    (run / "system").mkdir(parents=True)
    (case / "model").mkdir(parents=True)
    patch = "Ceiling_patch_1"
    manifest = {
        "schemaVersion": 4, "runId": "run_x", "sourceProject": {"contentHash": "h"},
        "simulationConfig": {"monitoring": {"acuReturnTemperature": [{"patch": patch}]}},
        "boundaryBindings": {"b": {
            "patchName": patch, "solidName": patch, "objectPath": object_path,
            "role": role, "bcType": "acu_return"}},
    }
    payload = json.dumps(manifest).encode()
    (case / "model" / "x.cfdrun").write_bytes(payload)
    (run / "air-run-execution-receipt.json").write_text(json.dumps({
        "format": "air-run-execution-receipt/v1", "status": "accepted",
        "result": {"cfdrunRelativePath": "model/x.cfdrun", "runId": "run_x",
                   "cfdrunSha256": hashlib.sha256(payload).hexdigest(),
                   "projectContentHash": "h"}}))
    (run / "system" / "controlDict").write_text(
        "functions\n{\n    avgT_%s\n    {\n        type surfaceFieldValue;\n"
        "        enabled true;\n        writeControl timeStep;\n        writeInterval 1;\n"
        "        regionType patch;\n        name %s;\n        operation areaAverage;\n"
        "        fields (T);\n    }\n}\n" % (patch, patch))
    out = run / "postProcessing" / ("avgT_" + patch) / "0"
    out.mkdir(parents=True)
    rows = "".join("%d\t%.6e\n" % (i, 295.15 - 5.0 * min(i, 20) / 20) for i in range(1, N + 1))
    out.joinpath("surfaceFieldValue.dat").write_text(
        "# Region type : patch %s\n# Faces       : 25\n# Area        : 9.8e-02\n"
        "# Scale factor: 1.000000e+00\n# Time        \tareaAverage(T)\n%s" % (patch, rows))
    return run


def _verdict(run: Path):
    return configured_return_patch_verdict(
        run, final_iteration=float(N), expected_iterations=[float(i) for i in range(1, N + 1)])


def test_ceiling_grille_return_is_a_true_return(tmp_path):
    v = _verdict(_case(tmp_path, "constructions.floors.m_6"))
    assert v["status"] != "NO_SENSORS", v["reasons"]
    assert "Ceiling_patch_1" in v["probes"]


def test_a_non_return_binding_is_still_refused(tmp_path):
    v = _verdict(_case(tmp_path, "constructions.floors.m_6", role="wall"))
    assert v["status"] == "NO_SENSORS"
    assert any("not one bound true ACU return" in r for r in v["reasons"])
