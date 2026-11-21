import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from cfdproj_migrate import load_cfdproj

def test_loads_v2_file_as_v3(tmp_path):
    import json
    f = tmp_path / "x.cfdproj"
    f.write_text(json.dumps({"schemaVersion": 2, "models": [{"id": "a", "kind": "rack"}], "cfdRoom": {"height": 3}}))
    d = load_cfdproj(f)
    assert d["schemaVersion"] == 3
    assert d["construction"][0]["id"] == "a"
