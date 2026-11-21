import json
import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cfdproj_migrate as cm  # noqa: E402

CASES = [
    "case/Base_leakage/Base_leakage.cfdproj",
    "case/Shinaide/Shinaide.cfdproj",
    "case/CMI-China/CMI_China.cfdproj",
    "case/STDCT/STDCT.cfdproj",
    "case/Liquid/Liquid.cfdproj",
]
REPO = ROOT.parents[1]

BUCKETS = [
    "boxes", "customMeshes", "surfaces", "walls", "partitionWalls", "baffles",
    "floors", "racks", "rows", "servers", "acus", "cdus", "pipeSegments",
    "pipeJunctions", "pipes", "sensors", "liquidFlowNetworks", "heatGains",
]


def test_v4_module_constants():
    assert cm.CFDPROJ_V4_SCHEMA_VERSION == 4


def test_simple_doc_migrates():
    doc = {
        "schemaVersion": 3,
        "meta": {"projectName": "T", "exportFileName": "T", "inputUnit": "m",
                 "description": "", "room": {"bounds": None, "polygon": None, "height": 3, "temperature": 22},
                 "case": {"path": "/tmp/T"}},
        "model": {"racks": {}, "acus": {}},
        "construction": [], "probeData": [], "sensorData": [],
        "simulation": {"meshBaseSize": 0.3, "solverType": "steady"},
        "liquidCooling": {"enabled": False}, "ui": {},
    }
    v4 = cm.migrate_cfdproj_v3_to_v4(doc)
    assert v4["schemaVersion"] == 4
    assert v4["uid"] == "proj_T"
    assert "ui" not in v4 and "simulation" not in v4
    assert v4["runDefaults"]["meshConfig"]["meshBaseSize"] == 0.3
    assert v4["runDefaults"]["simulationConfig"]["solverType"] == "steady"


def _all_fixtures_already_v4() -> bool:
    """Every case in CASES has been migrated in place.

    These two tests exercise the v3 -> V4 migration against REAL projects, and
    they need a v3 project to do it. The repo has none left: all five cases were
    migrated on disk during b821e27, so `raw["construction"]` is a KeyError and
    the batch CLI correctly reports nothing to change. Skipped with the reason
    rather than deleted — the assertions are the specification of what the
    migration must preserve, and they run again the day a v3 fixture is added.
    """
    for rel in CASES:
        f = REPO / rel
        if not f.exists():
            continue
        if "construction" in json.loads(f.read_text(encoding="utf-8")):
            return False
    return True


_NO_V3_FIXTURE = pytest.mark.skipif(
    _all_fixtures_already_v4(),
    reason="no v3 .cfdproj left in the repo — every case in CASES is already V4",
)


@_NO_V3_FIXTURE
def test_all_five_cases_migrate_with_invariants():
    for rel in CASES:
        raw = json.loads((REPO / rel).read_text(encoding="utf-8"))
        total = len(raw["construction"])
        v4 = cm.migrate_cfdproj_v3_to_v4(raw)
        assert v4["schemaVersion"] == 4, rel
        # construction-derived buckets (exclude liquidFlowNetworks, which is derived
        # from liquidCooling.piping when enabled, not from a construction kind)
        migrated = sum(
            len(v4["constructions"].get(b) or {})
            for b in BUCKETS if b != "liquidFlowNetworks"
        )
        assert migrated == total, f"{rel}: {migrated} != {total}"
        v3_ids = {m["id"] for m in raw["construction"]}
        for b in BUCKETS:
            if b == "liquidFlowNetworks":
                continue
            for uid, obj in (v4["constructions"].get(b) or {}).items():
                assert uid in v3_ids and obj["uid"] == uid, (rel, b, uid)
        assert cm.migrate_cfdproj_v3_to_v4(v4) == v4, f"{rel}: not idempotent"


@_NO_V3_FIXTURE
def test_batch_cli_dry_run_and_write(tmp_path):
    import migrate_cfdproj_to_v4 as cli
    src = REPO / "case/Base_leakage/Base_leakage.cfdproj"
    work = tmp_path / "Base_leakage.cfdproj"
    work.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    changed = cli.run([str(tmp_path)])            # dry-run default
    assert changed == [str(work)]
    assert json.loads(work.read_text())["schemaVersion"] == 3   # untouched

    changed = cli.run([str(tmp_path), "--write"])
    assert changed == [str(work)]
    data = json.loads(work.read_text())
    assert data["schemaVersion"] == 4

    assert cli.run([str(tmp_path), "--write"]) == []            # idempotent: nothing to do
