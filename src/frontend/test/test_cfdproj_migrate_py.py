import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from cfdproj_migrate import migrate_v2_to_v3

V2 = {
    "schemaVersion": 2, "projectName": "Base_leakage", "inputUnit": "m",
    "bounds": {"x": {"min": 0, "max": 4.4}}, "roomPolygon": {"vertices": [[0, 0], [4, 0], [4, 6]], "height": 3.6},
    "models": [{"id": "m_1", "kind": "rack", "name": "R1"}],
    "sensorPoints": [{"id": "s1", "x": 1, "y": 2, "z": 3, "enabled": True}],
    "cfdRoom": {"height": 3.6, "roomTemperature": 22, "meshBaseSize": 0.34},
    "dctwinConfig": {"casePath": "/abs/case", "activeJobId": "j1"},
    "displayMode": "air", "snapFace": True, "selection": {"type": "project"},
    "liquidCooling": {"enabled": False},
}

def test_sections_and_nesting():
    v3 = migrate_v2_to_v3(V2)
    assert v3["schemaVersion"] == 3
    assert v3["meta"]["projectName"] == "Base_leakage"
    assert v3["meta"]["room"]["height"] == 3.6
    assert v3["meta"]["room"]["temperature"] == 22
    assert v3["meta"]["case"]["path"] == "/abs/case"
    assert v3["construction"][0]["fromModel"] is None
    assert v3["model"] == {"racks": {}, "acus": {}}
    assert v3["probeData"][0]["id"] == "s1"
    assert v3["sensorData"] == []
    assert "height" not in v3["simulation"] and "roomTemperature" not in v3["simulation"]
    assert v3["ui"]["session"]["activeJobId"] == "j1"
    assert "casePath" not in v3["ui"]["session"]

def test_idempotent():
    v3 = migrate_v2_to_v3(V2)
    assert migrate_v2_to_v3(v3) == v3

def test_absent_version_migrates():
    raw = dict(V2); del raw["schemaVersion"]
    assert migrate_v2_to_v3(raw)["schemaVersion"] == 3
