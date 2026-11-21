"""Project tools on stand-ins for agent.scripted_api: edits reach build_project, reads leave nothing behind."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.scripted_api as api
from agent.scripted_api import identity as api_identity
from agent.scripted_api.headless import HeadlessCompileError

from agent.tools import edits as E
from agent.tools import project as tools

REPO = Path(__file__).resolve().parents[3]
SINGLE_RACK = REPO / "case/SingleRack/SingleRack.cfdproj"


@pytest.fixture
def fake_build(monkeypatch, tmp_path):
    calls = []

    def build_project(steps, *, out, base=None, overwrite=False, timeout=600.0):
        calls.append({"steps": list(steps), "out": Path(out), "base": base, "overwrite": overwrite})
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}")
        results = []
        for index, step in enumerate(steps):
            results.append({"index": index, "verb": step["verb"],
                            "result": json.loads(SINGLE_RACK.read_text()) if step["verb"] == "getProject"
                            else f"r{index}"})
        return {"format": "dcdtuni.api.build/v1", "status": "succeeded", "failure": None, "out": str(out),
                "steps": results, "selfCheck": {"ok": True}, "sha256": "f" * 64}
    monkeypatch.setattr(api, "build_project", build_project)
    monkeypatch.setattr(tools, "SCRATCH_ROOT", tmp_path / "scratch")
    return calls


def test_edit_project_compiles_edits_and_builds_once(fake_build, tmp_path):
    out = tmp_path / "out" / "X.cfdproj"
    report = tools.edit_project(SINGLE_RACK, [E.MoveModel("m_7", 1., 0., 2.), E.RemoveModels(["m_14"])],
                                out=out, build_verbs=["setModelLocation", "removeModels"])
    assert report["status"] == "succeeded"
    assert report["edits"] == [E.MoveModel("m_7", 1., 0., 2.).to_dict(), E.RemoveModels(["m_14"]).to_dict()]
    (call,) = fake_build
    assert call["base"] == str(SINGLE_RACK.resolve()) and call["out"] == out
    assert [s["verb"] for s in call["steps"]] == ["setModelLocation", "removeModels"]


def test_edit_project_needing_a_missing_verb_builds_nothing(fake_build, tmp_path):
    with pytest.raises(E.ContractVerbMissing):
        tools.edit_project(SINGLE_RACK, [E.SetPumpSpeed("c", .5)], out=tmp_path / "X.cfdproj", build_verbs=[])
    assert fake_build == []


def test_edit_project_reads_the_materialized_project_only_when_an_edit_needs_it(fake_build, tmp_path):
    report = tools.edit_project(SINGLE_RACK, [E.SetServerLoad("m_7", "e_1", power_w=2000.)],
                                out=tmp_path / "a.cfdproj", build_verbs=["updateRackServerCfd"])
    assert report["status"] == "succeeded" and len(fake_build) == 1
    tools.edit_project(SINGLE_RACK, [E.SetRackPower("m_7", 5000., split="equal")], out=tmp_path / "b.cfdproj",
                       build_verbs=["updateRackServerCfd"])
    assert [c["steps"][0]["verb"] for c in fake_build[1:]] == ["getProject", "updateRackServerCfd"]


def test_query_project_returns_step_results_and_removes_its_scratch(fake_build, tmp_path):
    results = tools.query_project(SINGLE_RACK, [{"verb": "allModels"}, {"verb": "rackServers", "args": ["m_7"]}])
    assert results == ["r0", "r1"]
    scratch = fake_build[0]["out"]
    assert not scratch.exists() and not scratch.parent.exists()
    assert scratch.is_relative_to(tmp_path / "scratch")


def test_query_project_raises_on_a_failed_build(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "SCRATCH_ROOT", tmp_path / "scratch")
    monkeypatch.setattr(api, "build_project", lambda steps, **kw: {
        "status": "failed", "failure": {"stage": "build", "reason": "step 0 (allModels): returned null"}, "steps": []})
    with pytest.raises(RuntimeError, match="returned null"):
        tools.query_project(SINGLE_RACK, [{"verb": "allModels"}])
    assert not any((tmp_path / "scratch").iterdir())


def test_project_identity_matches_the_api_hashes():
    identity = tools.project_identity(SINGLE_RACK)
    raw = SINGLE_RACK.read_bytes()
    doc = json.loads(raw)
    assert identity["sha256"] == api_identity.sha256_bytes(raw)
    assert identity["canonicalSha256"] == api_identity.canonical_sha256(doc)
    assert identity["schemaVersion"] == doc["schemaVersion"] and identity["uid"] == doc["uid"]
    assert identity["path"] == str(SINGLE_RACK.resolve())


@pytest.fixture
def fake_export(monkeypatch):
    calls = []

    def export_air_case(cfdproj, *, run_settings=None, solver_type=None, timestamp=None, mesh_patches=None):
        calls.append(Path(cfdproj))
        doc = json.loads(Path(cfdproj).read_text())
        if doc.get("refuse"):
            raise HeadlessCompileError("duplicate entity names")
        return {"cfdrunFilename": "X.cfdrun", "cfdrunJson": '{"version": 2}', "stlFiles": {"a.stl": "solid a"},
                "manifestErrors": doc.get("errors", []), "preflight": {"seedCollisions": doc.get("seeds", [])},
                "preflightStops": doc.get("stops", []), "hybrid": {"hasHybridFanServer": False, "budgetVersion": None},
                "simulationMode": {"mode": "steady"}}
    monkeypatch.setattr(api, "export_air_case", export_air_case)
    return calls


def test_validate_project_goes_through_the_api_export(fake_export, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_ROOT", tmp_path / "scratch")
    report = tools.validate_project(SINGLE_RACK)
    assert report["ok"] is True and report["errors"] == [] and report["via"] == "agent.scripted_api.export_air_case"
    stopped = tools.validate_project({"constructions": {"x": {}}, "stops": ["roomWallGaps"], "seeds": ["s"]})
    assert stopped["ok"] is True and stopped["preflightStops"] == ["roomWallGaps"]
    assert stopped["seedCollisions"] == ["s"] and stopped["blockers"] == ["preflight:roomWallGaps"]
    bad = tools.validate_project({"constructions": {}, "errors": [{"path": "x", "message": "bad"}]})
    assert bad["ok"] is False and bad["errors"] == [{"path": "x", "message": "bad"}]
    refused = tools.validate_project({"refuse": True})
    assert refused["ok"] is False and "duplicate entity names" in refused["errors"][0]["message"]
    assert fake_export[0] == SINGLE_RACK.resolve()
    assert not any((tmp_path / "scratch").iterdir())


def test_export_liquid_is_the_api_liquidrun_export(monkeypatch):
    import inspect
    seen = []
    signature = inspect.signature(api.export_liquidrun)

    def export_liquidrun(*args, **kwargs):
        signature.bind(*args, **kwargs)
        seen.append((args, kwargs))
        return {"liquidrun": {"version": 4}, "controllers": [], "simulationMode": "transient"}
    monkeypatch.setattr(api, "export_liquidrun", export_liquidrun)
    out = tools.export_liquid(SINGLE_RACK, mode="transient", duration_s=20.)
    assert out["liquidrun"] == {"version": 4}
    (args, kwargs), = seen
    assert args == (SINGLE_RACK,) and kwargs == {"mode": "transient", "duration_s": 20., "solver_type": None}


def _air_export_reply(files, *, declared=None):
    manifest = {"sourceProject": {"contentHash": "c" * 64, "exportedAt": "2026-10-25T00:00:00.000Z"},
                "geometryExport": {"stlFiles": {
                    f"f{i}": {"path": name, "contentHash": digest}
                    for i, (name, digest) in enumerate(sorted((declared if declared is not None else {
                        name: api_identity.sha256_bytes(text.encode()) for name, text in files.items()}).items()))}}}
    return {"cfdrunFilename": "X.cfdrun", "cfdrunJson": json.dumps(manifest), "stlFiles": dict(files),
            "manifestErrors": [], "preflight": {}, "preflightStops": [], "hybrid": {"hasHybridFanServer": True}}


def test_export_air_writes_the_manifest_and_the_bound_stl_batch(monkeypatch, tmp_path):
    files = {"merged_acu.stl": "solid a\nendsolid a\n", "room_wall.stl": "solid w\nendsolid w\n"}
    seen = []

    def export_air_case(cfdproj, *, run_settings=None, solver_type=None, timestamp=None, mesh_patches=None):
        seen.append({"cfdproj": Path(cfdproj), "timestamp": timestamp, "run_settings": run_settings})
        return _air_export_reply(files)
    monkeypatch.setattr(api, "export_air_case", export_air_case)
    out = tmp_path / "air"
    identity = tools.export_air(SINGLE_RACK, out_dir=out, timestamp="2026-10-25T00:00:00.000Z")
    assert seen == [{"cfdproj": SINGLE_RACK.resolve(), "timestamp": "2026-10-25T00:00:00.000Z", "run_settings": None}]
    assert sorted(p.name for p in out.iterdir()) == ["air.cfdrun", "merged_acu.stl", "room_wall.stl"]
    assert identity["stlSha256"] == {n: api_identity.sha256_bytes(t.encode()) for n, t in files.items()}
    assert identity["cfdrunSha256"] == api_identity.sha256_bytes((out / "air.cfdrun").read_bytes())
    assert identity["projectContentHash"] == "c" * 64 and identity["timestamp"] == "2026-10-25T00:00:00.000Z"
    assert identity["cfdproj"] == tools.project_identity(SINGLE_RACK) and identity["via"] == "agent.scripted_api.export_air_case"
    with pytest.raises(FileExistsError):  # a used directory is never written into
        tools.export_air(SINGLE_RACK, out_dir=out)


@pytest.mark.parametrize("declared", [{"merged_acu.stl": "0" * 64},                       # bytes not the bound ones
                                      {"merged_acu.stl": None, "extra.stl": "0" * 64}])    # batch differs
def test_export_air_refuses_unbound_stl_bytes_and_writes_nothing(monkeypatch, tmp_path, declared):
    files = {"merged_acu.stl": "solid a\nendsolid a\n"}
    declared = {n: d if d is not None else api_identity.sha256_bytes(files[n].encode()) for n, d in declared.items()}
    monkeypatch.setattr(api, "export_air_case", lambda cfdproj, **kw: _air_export_reply(files, declared=declared))
    with pytest.raises(ValueError, match="STL"):
        tools.export_air(SINGLE_RACK, out_dir=tmp_path / "air")
    assert not (tmp_path / "air").exists()


def test_export_air_through_the_real_headless_export(tmp_path):
    identity = tools.export_air(SINGLE_RACK, out_dir=tmp_path / "air", timestamp="2026-10-25T00:00:00.000Z")
    manifest = json.loads((tmp_path / "air" / "air.cfdrun").read_text())
    declared = {row["path"]: row["contentHash"] for row in manifest["geometryExport"]["stlFiles"].values()}
    assert identity["stlSha256"] == declared and declared
    for name, digest in declared.items():
        assert api_identity.sha256_bytes((tmp_path / "air" / name).read_bytes()) == digest
    again = tools.export_air(SINGLE_RACK, out_dir=tmp_path / "again", timestamp="2026-10-25T00:00:00.000Z")
    assert again["cfdrunSha256"] == identity["cfdrunSha256"] and again["stlSha256"] == identity["stlSha256"]
