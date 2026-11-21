"""Monitor choices retain exact physical identities for V1 and V2+ manifests."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def mesh(case, ranks):
    for rank, names in enumerate(ranks):
        root = case / "Mesh" / f"processor{rank}" / "constant" / "polyMesh"
        root.mkdir(parents=True)
        for name in ("points", "faces", "owner", "neighbour"):
            (root / name).write_text("mock complete mesh")
        (root / "boundary").write_text(
            str(len(names)) + "\n(\n" +
            "".join("    " + name + "\n    {\n        type patch;\n        nFaces 1;\n        startFace 0;\n    }\n" for name in names) +
            ")\n")


def response(monkeypatch, case, manifest):
    model = case / "model"
    model.mkdir()
    (model / "project.cfdrun").write_text(json.dumps(manifest))
    replies = []
    monkeypatch.setattr(server, "CASE_DIR", case)
    monkeypatch.setattr(server, "_send_json", lambda _, status, result: replies.append((status, result)))
    server.handle_server_patches(object())
    assert replies[0][0] == 200
    return replies[0][1]


@pytest.mark.parametrize("version", [2, 3, 4])
def test_bound_acu_returns_are_discovered_from_meshgeometry_and_each_rank_once(monkeypatch, tmp_path, version):
    returns = [f"ACU_0{i}_Back_patch" for i in range(1, 5)]
    outlets = ["Rack_A_srv1_outlet_p0", "Rack_A_srv1_outlet_p1"]
    mesh(tmp_path, [outlets + returns[:3] + ["ACU_01_Front_patch"], returns[1:]])
    acus = {f"ACU_0{i}": {"returnFaces": [{"solidName": returns[i-1]}]} for i in range(1, 5)}
    bindings = {f"bb_{name}": {"patchName": name, "solidName": name, "role": "acu_return"} for name in returns}
    bindings["supply"] = {"patchName": "ACU_01_Front_patch", "role": "acu_supply"}
    bindings["absent"] = {"patchName": "not_in_mesh", "role": "acu_return"}
    result = response(monkeypatch, tmp_path, {
        "schemaVersion": version, "meshGeometry": {"acus": acus}, "boundaryBindings": bindings,
    })
    assert result["patches"] == [
        {"patch": outlets[0], "shortId": "Rack_A_srv1_p0", "type": "outlet"},
        {"patch": outlets[1], "shortId": "Rack_A_srv1_p1", "type": "outlet"},
    ]
    assert result["acuReturnPatches"] == [
        {"patch": name, "shortId": f"ACU_0{i}", "type": "return"} for i, name in enumerate(returns, 1)
    ]


def test_binding_patchname_is_authoritative_and_solidname_only_supplies_label(monkeypatch, tmp_path):
    mesh(tmp_path, [["actual_return", "ACU_Front_patch", "server_OUTLET_p2"]])
    result = response(monkeypatch, tmp_path, {
        "meshGeometry": {"acus": {"source-device": {"name": "ACU West", "returnFaces": [{"solidName": "geometry_return"}]}}},
        "boundaryBindings": {
            "return": {"patchName": "actual_return", "solidName": "geometry_return", "bcType": "acu_return"},
            "duplicate": {"patchName": "actual_return", "solidName": "geometry_return", "role": "acu_return"},
            "supply": {"patchName": "ACU_Front_patch", "role": "acu_supply"},
        },
    })
    assert result["acuReturnPatches"] == [{"patch": "actual_return", "shortId": "ACU West", "type": "return"}]
    assert result["patches"] == [{"patch": "server_OUTLET_p2", "shortId": "server_p2", "type": "outlet"}]


def test_legacy_acu_return_faces_stay_supported_and_nonmesh_faces_are_not_offered(monkeypatch, tmp_path):
    mesh(tmp_path, [["ACU_Return", "server_inlet_left_p0"]])
    result = response(monkeypatch, tmp_path, {
        "schemaVersion": 1, "acus": [{"name": "Legacy ACU", "returnFaces": [
            {"solidName": "ACU_Return"}, {"solidName": "missing_face"}]}],
    })
    assert result["acuReturnPatches"] == [{"patch": "ACU_Return", "shortId": "Legacy ACU", "type": "return"}]
    assert result["patches"] == [{"patch": "server_inlet_left_p0", "shortId": "server_left_p0", "type": "inlet"}]


def test_binding_without_meshgeometry_uses_source_identity(monkeypatch, tmp_path):
    mesh(tmp_path, [["ACU_Return"]])
    result = response(monkeypatch, tmp_path, {
        "boundaryBindings": {"return": {
            "patchName": "ACU_Return", "objectPath": "constructions.acus.m_17", "role": "acu_return"}},
    })
    assert result["acuReturnPatches"] == [{"patch": "ACU_Return", "shortId": "m_17", "type": "return"}]
