"""JS and Python cfdproj v4->v5 migration and t = 0 projection agree on every case.

Value parity (integer-valued floats collapsed), tracks compared in identity
order: JavaScript orders integer-like object keys numerically, Python keeps
insertion order, so the list order may differ while the content may not.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cfdproj_transient as T  # noqa: E402

JS = str(ROOT / "shared" / "js" / "cfdproj_transient.js")


def _cases():
    """Every TRACKED case project. Git-ignored local trees are not cases: case/Slab_dataset
    holds about a thousand schemaVersion 2 dataset projects the v4->v5 migration never reads."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z", "--", "case/*.cfdproj"], cwd=REPO,
                                capture_output=True, check=True).stdout.decode().split("\0")
        paths = [REPO / p for p in listed if p]
    except (OSError, subprocess.CalledProcessError):  # not a checkout: every project on disk
        paths = list(REPO.glob("case/**/*.cfdproj"))
    out = []
    for p in sorted(paths):
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - unreadable/cloud-only files are not cases
            continue
        out.append(p)
    return out


def _norm(o):
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_norm(v) for v in o]
    if isinstance(o, float) and o.is_integer():
        return int(o)
    return o


def _canon(doc):
    doc = json.loads(json.dumps(doc))
    tr = doc.get("transient")
    if isinstance(tr, dict) and isinstance(tr.get("tracks"), list):
        tr["tracks"].sort(key=lambda x: (x["modelUid"], x["param"], x["target"]))
    return json.dumps(_norm(doc), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _js(path):
    snippet = (
        "const T = require(process.argv[1]);"
        "const raw = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));"
        "const {doc, report} = T.migrateCfdprojV4toV5WithReport(raw);"
        "const live = T.materializeAt(JSON.parse(JSON.stringify(doc)), 0);"
        "process.stdout.write(JSON.stringify({doc, report, live}));"
    )
    out = subprocess.run(["node", "-e", snippet, JS, str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


CASES = _cases()


def test_there_are_cases():
    assert len(CASES) > 10


@pytest.mark.parametrize("path", CASES, ids=[str(p.relative_to(REPO)) for p in CASES])
def test_migration_and_projection_parity(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    js = _js(path)
    doc, report = T.migrate_v4_to_v5_with_report(json.loads(json.dumps(raw)))
    assert _canon(doc) == _canon(js["doc"])
    assert report == js["report"]
    assert _canon(T.project_at(doc, 0)) == _canon(js["live"])
    assert T.validate_transient(doc["transient"]) == []


def _v5(solver_type=None, dae_mode=None, mode="steady", **domains):
    run = {"simulationConfig": {"solverType": solver_type} if solver_type else {}}
    if dae_mode is not None:
        run["liquid"] = {"experiment": {"dae": {"analysis": {"mode": dae_mode}}}}
    tr = {"mode": mode, "durationS": 600, "gridS": 1, "start": {"kind": "uniform"}, "tracks": [], **domains}
    return {"schemaVersion": 5, "runDefaults": run, "transient": tr}


DOMAIN_CASES = {
    # A migrated Shinaide-style hybrid: air steady, the DAE marches in time.
    "hybrid": (_v5("steady", "transient", "transient"), {"air": "steady", "liquid": "transient"}),
    "air_only": (_v5("transient", None, "transient"), {"air": "transient", "liquid": "transient"}),
    "air_transient_dae_steady": (_v5("transient", "steady", "transient"), {"air": "transient", "liquid": "steady"}),
    "liquid_without_dae": (_v5("steady", None, "transient"), {"air": "steady", "liquid": "transient"}),
    "steady": (_v5("steady", "steady", "steady"), {"air": "steady", "liquid": "steady"}),
    # A DAE that marched in time under a steady timeline ran without it: stays steady.
    "dae_transient_timeline_steady": (_v5("steady", "transient", "steady"), {"air": "steady", "liquid": "steady"}),
    "explicit": (_v5("transient", "transient", "transient", airMode="steady", liquidMode="transient"),
                 {"air": "steady", "liquid": "transient"}),
}


@pytest.mark.parametrize("name", sorted(DOMAIN_CASES))
def test_domain_modes_parity(name):
    doc, expected = DOMAIN_CASES[name]
    snippet = (
        "const T = require(process.argv[1]); const doc = JSON.parse(process.argv[2]);"
        "const modes = T.domainModes(doc); T.withDomainModes(doc);"
        "process.stdout.write(JSON.stringify({modes, tr: doc.transient, errs: T.validateTransient(doc.transient)}));"
    )
    js = json.loads(subprocess.run(["node", "-e", snippet, JS, json.dumps(doc)],
                                   capture_output=True, text=True, check=True).stdout)
    py_doc = json.loads(json.dumps(doc))
    assert T.domain_modes(py_doc) == expected == js["modes"]
    T.with_domain_modes(py_doc)
    assert py_doc["transient"] == js["tr"]
    assert py_doc["transient"]["mode"] == T.mode_of_domains(expected["air"], expected["liquid"])
    assert T.validate_transient(py_doc["transient"]) == [] == js["errs"]


def test_a_mode_that_contradicts_the_domains_is_invalid_in_both():
    tr = {"mode": "steady", "airMode": "transient", "liquidMode": "steady", "durationS": 600, "gridS": 1,
          "start": {"kind": "uniform"}, "tracks": []}
    snippet = "const T = require(process.argv[1]); process.stdout.write(JSON.stringify(T.validateTransient(JSON.parse(process.argv[2]))));"
    js = json.loads(subprocess.run(["node", "-e", snippet, JS, json.dumps(tr)],
                                   capture_output=True, text=True, check=True).stdout)
    assert T.validate_transient(tr) == js
    assert [e["path"] for e in js] == ["transient.mode"]
