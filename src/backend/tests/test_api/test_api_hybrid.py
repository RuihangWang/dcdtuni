"""Hybrid air/liquid runs without a server: the headless compiler and the private server are stand-ins.

What is pinned: one server session runs the liquid DAE session first
(``POST /api/liquid/runs`` -> ``start`` -> poll until completed), then the
mesh without ``hybridHeat``, then the solve carrying
``hybridHeat={project, liquidRunId, sampleIndex}`` exactly as the editor's
``buildRunRequest('solve')`` builds it (run/runSubmit.ts); the heat ledger the
server writes is recorded in result.json; the editor's own hybrid gates are
refused at the input stage. A real server run is in the real-run evidence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.scripted_api import air
from backend.tests.test_api.test_api_air_stages import STL, _manifest

CANONICAL = {"schemaVersion": 5, "uid": "proj_h", "name": "H", "runDefaults": {"hybridHeatBudget": {"version": 2}}}
LEDGER = {"format": "hybrid-air-heat-ledger/v1", "liquid_run_id": "run1", "sample_index": 3, "time_s": 6.0,
          "totals": {"air_residual_W": 1200.0}, "servers": [{"uid": "e_1", "air_W": 1200.0}]}


def _liquid_result(samples=4):
    edges, nodes = ["pump", "pipe"], ["a", "b"]
    return {"edge_ids": edges, "node_ids": nodes, "edge_from": ["a", "b"], "edge_to": ["b", "a"],
            "edge_types": ["pump", "pipe"], "edge_D_m": [None, 0.02], "node_positions_m": [[0, 0, 0], [1, 0, 0]],
            "edge_flow_kg_s": [[0.5 + i * 0.01 for i in range(samples)], [0.5 + i * 0.01 for i in range(samples)]],
            "edge_outlet_temp_K": [[300.0] * samples, [301.0] * samples],
            "node_pressure_Pa": [[2e5] * samples, [1.9e5] * samples],
            "node_temperature_K": [[300.0] * samples, [301.0] * samples],
            "node_observable": [[True] * samples, [True] * samples], "edge_stagnant": [False, False],
            "chip_temp_K": [[330.0 + i for i in range(samples)]], "time_s": [2.0 * i for i in range(samples)],
            "fluid": {"rho_kg_m3": 997.0}, "diagnostics": [{"energy_balance_W": 0.0, "heat_input_W": 1e3}] * samples,
            "hx_edge_ids": [], "primary_return_K": [], "secondary_supply_K": [], "cp_details": [],
            "rack_summaries": []}


class Compiler:
    """Stands in for headless.compile_project: three operations, one document."""

    def __init__(self, *, budget=2, inherit=True, fan=True):
        self.calls = []
        self.flags = {"hasHybridFanServer": fan, "budgetVersion": budget}
        self.inherit = inherit

    def __call__(self, operation, project, project_path, *, timestamp=None, options=None, **_):
        self.calls.append({"operation": operation, "project": copy.deepcopy(project), "options": options or {}})
        if operation == "air-case":
            return {"cfdrunFilename": "H.cfdrun", "cfdrunJson": json.dumps(_manifest()), "stlFiles": dict(STL),
                    "manifestErrors": [], "modelCount": 3, "preflight": {"seedCollisions": []},
                    "preflightStops": [], "hybrid": dict(self.flags),
                    "simulationMode": {"mode": "transient", "airMode": "steady", "liquidMode": "transient"},
                    "implementation": {"node": "test"}}
        if operation == "liquidrun":
            return {"liquidrun": {"schemaVersion": 4, "version": "4.0", "time": {"N_t": 4, "dt_s": 2.0},
                                  "execution": {"backend": "pytorch", "device": "cuda", "dtype": "float64",
                                                "max_threads": 2}},
                    "controllers": [], "simulationMode": {"liquidMode": "transient"}, "implementation": {}}
        if operation == "hybrid-project":
            return {"project": copy.deepcopy(CANONICAL), "contentHash": "c" * 64, **self.flags,
                    "inheritResults": self.inherit, "implementation": {}}
        raise AssertionError(operation)


class HybridSession:
    instances: list = []

    def __init__(self, case_dir, work_dir, *, app="api", **_):
        self.case_dir, self.app = Path(case_dir), app
        self.bodies, self.requests, self.port, self.startup_seconds = [], [], 20000, 0.01
        self.command, self.stop_report, self.log_path = ["bash", "start_server.sh", "--app", app], {}, None
        self.polls = 0
        self.final_status = "completed"
        HybridSession.instances.append(self)

    def start(self):
        pass

    def stop(self):
        self.stop_report = {"freed": [{"port": 20000, "pids": [1], "signal": "TERM"}]}

    def call(self, method, path, payload=None, *, timeout=60.0, expect=(200,)):
        self.requests.append((method, path, copy.deepcopy(payload)))
        if (method, path) == ("POST", "/api/liquid/runs"):
            assert 201 in expect
            return {"run_id": "run1", "status": "ready", "frame": {"seq": 0}, "error": None}
        if (method, path) == ("POST", "/api/liquid/runs/run1/start"):
            return {"run_id": "run1", "status": "running", "frame": {"seq": 0}, "error": None}
        if (method, path) == ("GET", "/api/liquid/runs/run1"):
            self.polls += 1
            if self.polls < 2:
                return {"run_id": "run1", "status": "running", "frame": {"seq": 1}, "error": None}
            return {"run_id": "run1", "status": self.final_status, "frame": {"seq": 3, "time_s": 6.0},
                    "error": None if self.final_status == "completed" else {"message": "boom"}}
        if (method, path) == ("GET", "/api/liquid/runs/run1/result"):
            return _liquid_result()
        raise AssertionError((method, path))

    def submit_run(self, body):
        self.bodies.append(copy.deepcopy(body))
        if body.get("hybridHeat"):
            model = self.case_dir / "model"
            model.mkdir(parents=True, exist_ok=True)
            heat = body["hybridHeat"]
            ledger = {**LEDGER, "liquid_run_id": getattr(self, "ledger_run_id", heat["liquidRunId"]),
                      "sample_index": heat["sampleIndex"]}
            (model / "heat-ledger.json").write_text(json.dumps(ledger))
        return {"jobId": f"job{len(self.bodies)}", "cfdrunSha256": "sha"}

    def wait_job(self, job_id, *, timeout):
        body = self.bodies[int(job_id[3:]) - 1]
        if body.get("solveOnly"):
            run = self.case_dir / "log" / "2026-10-21-10-00-00_H"
            (run / "model").mkdir(parents=True, exist_ok=True)
            (run / "model" / "H.cfdrun").write_text(json.dumps({**_manifest(), "hybrid": True}))
            (run / "convergence.json").write_text(json.dumps({"verdict": "CONVERGED"}))
            return {"done": True, "returncode": 0, "solveAssessment": {"runDir": str(run)}}
        return {"done": True, "returncode": 0, "checkMeshOk": True, "failedChecks": 0, "meshWarnings": []}

    def job_log(self, job_id):
        return ["  cells: 1234"], None


@pytest.fixture
def stand_ins(monkeypatch):
    from agent.scripted_api import air_metrics, server_session
    HybridSession.instances = []
    compiler = Compiler()
    monkeypatch.setattr(air, "compile_project", compiler)
    monkeypatch.setattr(server_session, "ServerSession", HybridSession)
    seen = {}

    def metrics(run, manifest, project):
        seen["manifest"] = manifest
        return {"read": True}
    monkeypatch.setattr(air_metrics, "compute_air_metrics", metrics)
    compiler.seen = seen
    return compiler


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "H.cfdproj"
    path.write_text(json.dumps({"schemaVersion": 5, "runDefaults": {"meshConfig": {}, "simulationConfig": {},
                                                                     "hybridHeatBudget": {"version": 2},
                                                                     "liquid": {"inheritResults": True}}}))
    return path


def test_hybrid_runs_the_liquid_session_then_mesh_then_solve_with_its_heat(stand_ins, project):
    result = air.run_hybrid_case(project)
    assert result["status"] == "succeeded", result["failure"]
    assert result["kind"] == "hybrid"
    session, = HybridSession.instances
    # the liquid session ran on the same server, before any air job
    posted = session.requests[0]
    assert posted[:2] == ("POST", "/api/liquid/runs")
    # the compiled run, on the CPU unless asked (the saved project said cuda)
    assert posted[2] == {"liquidrun": {"schemaVersion": 4, "version": "4.0", "time": {"N_t": 4, "dt_s": 2.0},
                                       "execution": {"backend": "pytorch", "device": "cpu", "dtype": "float64",
                                                     "max_threads": 2}},
                         "controllers": []}
    assert result["stages"]["hybridExport"]["liquidOverrides"] == {"device": "cpu"}
    assert ("POST", "/api/liquid/runs/run1/start", {}) in session.requests
    mesh, solve = session.bodies
    assert mesh["meshOnly"] is True and "hybridHeat" not in mesh
    assert solve["solveOnly"] is True and solve["stlFiles"] == {}
    # the editor's hybrid body: canonical project, the run, its latest accepted sample
    assert solve["hybridHeat"] == {"project": CANONICAL, "liquidRunId": "run1", "sampleIndex": 3}
    liquid = result["stages"]["liquid"]
    assert liquid["runId"] == "run1" and liquid["status"] == "completed" and liquid["sampleIndex"] == 3
    assert liquid["runDir"] == str(project.parent / "Liquid" / "log" / "run-run1")
    assert result["metrics"]["heatLedger"] == LEDGER
    # the liquid network at the handed-over sample, in the same shape as the liquid API's
    network = result["metrics"]["liquid"]
    assert network["sampleIndex"] == 3 and network["timeS"] == 6.0
    assert [row["massFlowKgS"] for row in network["edges"]] == [0.53, 0.53]
    assert network["edges"][1]["direction"] == "b->a"
    # field read-back uses the manifest the solver ran (the server's hybrid compile), not the export
    assert stand_ins.seen["manifest"].get("hybrid") is True
    # every compile of one run sees the same document
    ops = [call["operation"] for call in stand_ins.calls]
    assert sorted(ops) == ["air-case", "hybrid-project", "liquidrun"]
    assert len({json.dumps(call["project"], sort_keys=True) for call in stand_ins.calls}) == 1


def test_an_explicit_sample_index_is_handed_over_and_checked(stand_ins, project):
    result = air.run_hybrid_case(project, sample_index=1)
    assert result["status"] == "succeeded", result["failure"]
    assert HybridSession.instances[0].bodies[-1]["hybridHeat"]["sampleIndex"] == 1
    result = air.run_hybrid_case(project, sample_index=9)
    assert result["failure"]["stage"] == "liquid" and "sample" in result["failure"]["reason"]
    assert len(HybridSession.instances[-1].bodies) == 0  # refused before the mesh


def test_a_failed_liquid_session_stops_before_the_air_jobs(stand_ins, project, monkeypatch):
    original = HybridSession.__init__

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.final_status = "failed"
    monkeypatch.setattr(HybridSession, "__init__", failing)
    result = air.run_hybrid_case(project)
    assert result["failure"]["stage"] == "liquid" and "failed" in result["failure"]["reason"]
    assert HybridSession.instances[0].bodies == []


@pytest.mark.parametrize("budget, inherit, fan, message", [
    (None, True, True, "Declare a fan hybrid IT budget"),
    (2, False, True, "inheritResults"),
])
def test_the_editor_hybrid_gates_are_refused_at_input(monkeypatch, project, budget, inherit, fan, message):
    from agent.scripted_api import air_metrics, server_session
    HybridSession.instances = []
    monkeypatch.setattr(air, "compile_project", Compiler(budget=budget, inherit=inherit, fan=fan))
    monkeypatch.setattr(server_session, "ServerSession", HybridSession)
    monkeypatch.setattr(air_metrics, "compute_air_metrics", lambda *a: {})
    for run in (air.run_hybrid_case, air.run_air_case):
        result = run(project)
        assert result["failure"]["stage"] == "input" and message in result["failure"]["reason"], result["failure"]
    assert HybridSession.instances == []


def test_air_runs_of_a_hybrid_project_take_the_hybrid_path(stand_ins, project):
    # no refusal any more: a solve of a hybrid project gets its liquid heat as in the editor
    result = air.run_air_case(project)
    assert result["status"] == "succeeded", result["failure"]
    assert result["kind"] == "hybrid" and result["stages"]["liquid"]["status"] == "completed"
    # a mesh needs no liquid session (the editor's mesh button sends no hybridHeat)
    mesh_only = air.run_air_case(project, stages=("mesh",))
    assert mesh_only["status"] == "succeeded" and "liquid" not in mesh_only["stages"]
    assert HybridSession.instances[-1].requests == []


def test_hybrid_needs_a_hybrid_project(monkeypatch, project):
    from agent.scripted_api import server_session
    HybridSession.instances = []
    monkeypatch.setattr(air, "compile_project", Compiler(budget=None, fan=False))
    monkeypatch.setattr(server_session, "ServerSession", HybridSession)
    result = air.run_hybrid_case(project)
    assert result["failure"]["stage"] == "input" and "not a hybrid project" in result["failure"]["reason"]
    with pytest.raises(ValueError):
        air.run_hybrid_case(project, stages=("mesh",))


def test_the_air_mode_reaches_every_compile_of_a_hybrid_run(stand_ins, project):
    air.run_hybrid_case(project, solver_type="transient")
    assert {call["operation"]: call["options"] for call in stand_ins.calls} == {
        "air-case": {"solverType": "transient", "monitoring": {"residuals": True, "acuReturnTemperature": "all"}},
        "liquidrun": {"solverType": "transient"}, "hybrid-project": {"solverType": "transient"}}


def test_the_liquid_device_and_threads_are_the_callers(stand_ins, project):
    air.run_hybrid_case(project, liquid_device=None, liquid_threads=4)
    execution = HybridSession.instances[-1].requests[0][2]["liquidrun"]["execution"]
    assert execution["device"] == "cuda" and execution["max_threads"] == 4


def test_a_server_refusal_of_the_liquid_run_fails_the_liquid_stage(stand_ins, project, monkeypatch):
    from agent.scripted_api.server_session import ServerError

    def refuse(self, method, path, payload=None, **kwargs):
        raise ServerError(400, {"error": "No CUDA GPUs are available"}, path)
    monkeypatch.setattr(HybridSession, "call", refuse)
    result = air.run_hybrid_case(project)
    assert result["failure"]["stage"] == "liquid"
    assert "No CUDA GPUs are available" in result["failure"]["reason"]
    assert HybridSession.instances[-1].bodies == []


def test_a_ledger_from_another_liquid_run_is_refused(stand_ins, project, monkeypatch):
    original = HybridSession.__init__

    def stale(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.ledger_run_id = "someone-else"
    monkeypatch.setattr(HybridSession, "__init__", stale)
    result = air.run_hybrid_case(project)
    assert result["failure"]["stage"] == "solve" and "someone-else" in result["failure"]["reason"]


def test_solve_only_of_a_hybrid_project_reuses_the_mesh_its_hybrid_mesh_source_binds(stand_ins, project):
    # the runner writes a hybrid project's mesh source to hybrid-mesh-source.json (hybrid-mesh-source/v2),
    # never source-surface-mesh-source.json, and reads that one before a hybrid solve (stlgen_runner.py)
    case = project.parent
    (case / "Mesh" / "constant" / "triSurface").mkdir(parents=True)
    (case / "mesh-quality-receipt.json").write_text(json.dumps(
        {"format": "air-mesh-quality/v1", "status": "accepted", "failedChecks": 0, "meshSha256": "m" * 64}))
    hashes = {key: row["contentHash"] for key, row in _manifest()["geometryExport"]["stlFiles"].items()}
    (case / "hybrid-mesh-source.json").write_text(json.dumps(
        {"format": "hybrid-mesh-source/v2", "exported_stl_hashes": hashes, "mesh_sha256": "m" * 64}))
    # the hybrid heat handoff measures the faces on the uploaded batch, which the mesh job archived
    refused = air.run_hybrid_case(project, stages=("solve",))
    assert refused["failure"]["stage"] == "input" and "log/air-inputs" in refused["failure"]["reason"]
    assert HybridSession.instances == []
    archive = case / "log" / "air-inputs" / "a1" / "Mesh" / "constant" / "triSurface"
    archive.mkdir(parents=True)
    for name, text in STL.items():
        (archive / name).write_text(text)
    for run in (air.run_hybrid_case, air.run_air_case):
        result = run(project, stages=("solve",))
        assert result["status"] == "succeeded", result["failure"]
        assert result["kind"] == "hybrid"
        assert result["stages"]["meshReuse"]["stlBatch"] == {"basis": "hybrid-mesh-source.json", "files": 2,
                                                              "match": True, "uploadedBatch": str(archive)}
        body, = HybridSession.instances[-1].bodies
        assert body["solveOnly"] is True and body["hybridHeat"]["liquidRunId"] == "run1"
    (case / "hybrid-mesh-source.json").write_text(json.dumps(
        {"format": "hybrid-mesh-source/v2", "exported_stl_hashes": {**hashes, "stl_001": "1" * 64},
         "mesh_sha256": "m" * 64}))
    refused = air.run_hybrid_case(project, stages=("solve",))
    assert refused["failure"]["stage"] == "input" and "stl_001" in refused["failure"]["reason"]
