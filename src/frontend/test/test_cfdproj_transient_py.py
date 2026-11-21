"""Unit tests of scripts/cfdproj_transient.py (mirror of test_cfdproj_transient.mjs)."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cfdproj_transient as T  # noqa: E402


def entry(key):
    return T.entry_of(key)


def rack():
    return {"uid": "r1", "constructions": {"servers": {
        "e_1": {"cooling": {"power": 1000}, "liquidCooling": {"cpCuPairs": [{"Q_W": 100}, {"Q_W": 50}]}},
        "e_2": {"cooling": {"power": 500}}}}}


def acu():
    return {"uid": "a1", "geometry": {"faces": {
        "front": {"patches": [{"id": "p_1", "physics": {"role": "supply", "temperature": 20, "volumeFlowRate": 4.2}}]},
        "back": {"patches": [{"id": "p_2", "physics": {"role": "return", "temperature": 15, "volumeFlowRate": 4.2}}]}}}}


def test_enumerate_and_get_set_delete():
    assert T.enumerate_targets(rack(), entry("server.power")) == [{"target": "e_1", "value": 1000}, {"target": "e_2", "value": 500}]
    assert T.enumerate_targets(rack(), entry("cp.heat")) == [{"target": "e_1/0", "value": 100}, {"target": "e_1/1", "value": 50}]
    assert T.enumerate_targets(acu(), entry("acu.supplyTemp")) == [{"target": "p_1", "value": 20}]
    assert T.enumerate_targets({"uid": "c1", "cooling": {}}, entry("cdu.returnPressure")) == [{"target": "", "value": 150}]
    assert T.enumerate_targets({"uid": "c1"}, entry("pump.rpm")) == [{"target": "", "value": 1}]
    m = acu()
    T.set_at(m, entry("acu.supplyTemp"), "p_1", 18)
    assert m["geometry"]["faces"]["front"]["patches"][0]["physics"]["temperature"] == 18
    T.delete_at(m, entry("acu.supplyTemp"), "p_1")
    assert "temperature" not in m["geometry"]["faces"]["front"]["patches"][0]["physics"]
    r = rack()
    T.set_at(r, entry("cp.heat"), "e_1/1", 75)
    assert r["constructions"]["servers"]["e_1"]["liquidCooling"]["cpCuPairs"][1]["Q_W"] == 75


KF = [{"t": 0, "value": 10}, {"t": 60, "value": 20}, {"t": 120, "value": 5, "rampS": 30}]


def test_value_at_and_corner_table():
    assert [T.value_at(KF, t) for t in (0, 59.9, 60, 90, 105, 120, 500)] == [10, 10, 20, 20, 12.5, 5, 5]
    assert T.corner_table(KF, 200) == [[0, 10], [60, 10], [60, 20], [90, 20], [120, 5], [200, 5]]


def test_validate():
    ok = {"mode": "transient", "durationS": 200, "gridS": 1, "start": {"kind": "uniform", "uniform": {}},
          "tracks": [{"modelUid": "r1", "param": "server.power", "target": "e_1", "keyframes": KF}]}
    assert T.validate_transient(ok) == []
    bad = copy.deepcopy(ok)
    bad["tracks"][0]["keyframes"] = [{"t": 0, "value": 10}, {"t": 60.5, "value": 20}, {"t": 50, "value": 1, "rampS": 90}]
    msgs = " | ".join(e["msg"] for e in T.validate_transient(bad))
    assert "grid" in msgs and "increasing" in msgs and "ramp" in msgs


def test_tracks_strip_materialise_round_trip():
    doc = {"constructions": {"racks": {"r1": rack()}, "acus": {"a1": acu()}}, "transient": T.default_transient({})}
    doc["transient"]["tracks"] = T.tracks_from_fields(doc)
    assert len(doc["transient"]["tracks"]) == 6
    original = copy.deepcopy(doc)
    T.strip_tracked_fields(doc)
    assert "power" not in doc["constructions"]["racks"]["r1"]["constructions"]["servers"]["e_1"]["cooling"]
    assert T.project_at(doc, 0)["constructions"] == original["constructions"]


def test_migration_curves_and_liquid():
    a = acu()
    a["timeVarying"] = {"supplyTemp": [{"t": 0, "value": 20}, {"t": 100, "value": 20}, {"t": 100.1, "value": 16}, {"t": 200, "value": 18}]}
    doc = T.migrate_to_v5({"schemaVersion": 4, "geometry": {"temperature": 22}, "constructions": {"acus": {"a1": a}}, "runDefaults": {}})
    trk = next(x for x in doc["transient"]["tracks"] if x["param"] == "acu.supplyTemp")
    assert trk["keyframes"] == [{"t": 0, "value": 20}, {"t": 100, "value": 16, "rampS": 0}, {"t": 200, "value": 18, "rampS": 99.9}]
    assert doc["transient"]["gridS"] == 0.1 and doc["schemaVersion"] == 5
