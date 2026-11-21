"""/api/mesh-change: the same surface with other mesh settings needs a new mesh; a view switch needs nothing."""
from __future__ import annotations

import base64
import json

from backend.tests.test_mesh_change import _walls, _write
from backend.tests.test_scano_ai import reconstructed_case
from server import _mesh_change


def _case(tmp_path, manifest):
    case = tmp_path / "case"
    reconstructed_case(case / "Mesh")
    batch = case / "log" / "air-inputs" / "abc"
    files = _write(batch / "Mesh" / "constant" / "triSurface", {"walls": _walls()})
    (batch / "model").mkdir(parents=True)
    (batch / "model" / "Room.cfdrun").write_text(json.dumps(manifest))
    (case / "Mesh" / "mesh-source-batch.json").write_text(json.dumps({"inputId": "abc"}))
    return case, {name: base64.b64encode(data).decode() for name, data in files.items()}


def test_mesh_settings_change_asks_for_a_new_mesh(tmp_path):
    meshed = {"meshConfig": {"meshBaseSize": 0.34, "meshEngine": "dcCart", "meshProcess": 8,
                             "locInMeshVisible": False}}
    case, stl = _case(tmp_path, meshed)
    shown = {"meshConfig": {**meshed["meshConfig"], "meshProcess": 24, "locInMeshVisible": True}}
    assert _mesh_change(case, stl, json.dumps(shown))["kind"] == "none"
    finer = {"meshConfig": {**meshed["meshConfig"], "meshBaseSize": 0.2}}
    result = _mesh_change(case, stl, json.dumps(finer))
    assert (result["kind"], result["reason"], result["settings"]) == ("geometry", "settings", ["meshBaseSize"])


def test_a_dccart_receipt_left_behind_does_not_hold_a_snappy_mesh(tmp_path):
    # Meshed once with dcCart, then with snappy: snappy writes no source-region receipt, so the old one stays,
    # and comparing against it asked for a refresh that neither a new mesh nor a patch update ever cleared.
    manifest = {"sourceProject": {"uid": "p1"}, "geometryExport": {"sourceRegions": {"r": {}}},
                "meshConfig": {"meshEngine": "snappy", "meshBaseSize": 0.34}}
    case, stl = _case(tmp_path, manifest)
    (case / "source-region-mesh-source.json").write_text(json.dumps({"meshInputIdentitySha256": "0" * 64}))
    assert _mesh_change(case, stl, json.dumps(manifest))["kind"] == "none"
    dccart = {**manifest, "meshConfig": {**manifest["meshConfig"], "meshEngine": "dcCart"}}
    case, stl = _case(tmp_path / "b", dccart)
    (case / "source-region-mesh-source.json").write_text(json.dumps({"meshInputIdentitySha256": "0" * 64}))
    assert _mesh_change(case, stl, json.dumps(dccart))["reason"] == "refresh"
