"""Air run stages without a server: the export and the private server are stand-ins.

``compile_project`` (the headless export) and ``ServerSession`` (the private
server) are replaced by recorders, so these tests pin what the API sends and
when it refuses -- mesh-only, solve-only on a reused mesh, the serial-solve
guard, the solver-type switch and the mesher identity -- without Node or
OpenFOAM. The real export and a real server are covered elsewhere
(test_api_headless_export.py, test_api_rundir_and_session.py).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent.scripted_api import air

STL = {"merged_acu.stl": "solid acu\nendsolid acu\n", "merged_rack_frame.stl": "solid rack\nendsolid rack\n"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest(*, engine="dcCart", parallel_solve=True, solve_process=2, source_surfaces=True):
    files = {f"stl_{i:03d}": {"path": name, "kind": "grouped_ascii", "contentHash": _sha(text)}
             for i, (name, text) in enumerate(sorted(STL.items()))}
    geometry = {"stlFiles": files}
    if source_surfaces:
        geometry["sourceSurfaces"] = {"acu": {"file": "stl_000"}}
    return {"schemaVersion": 4, "runId": "run_x",
            "meshConfig": {"meshEngine": engine, "parallelMesh": True, "meshProcess": 2},
            "simulationConfig": {"parallelSolve": parallel_solve, "solveProcess": solve_process,
                                 "solverType": "steady", "endTime": 5},
            "geometryExport": geometry}


class Recorder:
    """Stands in for headless.compile_project and remembers what it was asked."""

    def __init__(self, manifest=None, **extra):
        self.manifest = manifest or _manifest()
        self.extra = extra
        self.calls = []

    def __call__(self, operation, project, project_path, *, timestamp=None, options=None, **_):
        self.calls.append({"operation": operation, "project": project, "options": options or {}})
        reply = {"cfdrunFilename": "X.cfdrun", "cfdrunJson": json.dumps(self.manifest), "stlFiles": dict(STL),
                 "manifestErrors": [], "modelCount": 3, "preflight": {"seedCollisions": []},
                 "preflightStops": [], "hybrid": {"hasHybridFanServer": False, "budgetVersion": None},
                 "simulationMode": {"mode": "steady", "airMode": "steady", "liquidMode": "steady"},
                 "implementation": {"node": "test"}}
        reply.update(self.extra)
        return reply


class FakeSession:
    instances: list = []

    def __init__(self, case_dir, work_dir, *, app="api", gpu=None, **_):
        self.case_dir, self.app, self.gpu = Path(case_dir), app, gpu
        self.bodies, self.port, self.startup_seconds = [], 20000, 0.01
        self.command, self.stop_report, self.log_path = ["bash", "start_server.sh", "--app", app], {}, None
        FakeSession.instances.append(self)

    def start(self):
        pass

    def stop(self):
        self.stop_report = {"freed": [{"port": 20000, "pids": [1], "signal": "TERM"}]}

    def submit_run(self, body):
        self.bodies.append(body)
        return {"jobId": f"job{len(self.bodies)}", "cfdrunSha256": "sha"}

    def wait_job(self, job_id, *, timeout):
        body = self.bodies[int(job_id[3:]) - 1]
        if body.get("solveOnly"):
            run = self.case_dir / "log" / "2026-10-21-10-00-00_X"
            run.mkdir(parents=True, exist_ok=True)
            (run / "convergence.json").write_text(json.dumps({"verdict": "CONVERGED"}))
            return {"done": True, "returncode": 0, "solveAssessment": {"runDir": str(run)}}
        return {"done": True, "returncode": 0, "checkMeshOk": True, "failedChecks": 0, "meshWarnings": []}

    def job_log(self, job_id):
        body = self.bodies[int(job_id[3:]) - 1]
        if body.get("solveOnly"):
            return ["Using repo-built solver: /opt/bin/buoyantBoussinesqSimpleFoam_new"], None
        return ["  cells: 1234"], None


@pytest.fixture
def stand_ins(monkeypatch):
    from agent.scripted_api import air_metrics, server_session
    FakeSession.instances = []
    recorder = Recorder()
    monkeypatch.setattr(air, "compile_project", recorder)
    monkeypatch.setattr(server_session, "ServerSession", FakeSession)
    monkeypatch.setattr(air_metrics, "compute_air_metrics", lambda run, manifest, project: {"read": True})
    return recorder


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "X.cfdproj"
    path.write_text(json.dumps({"schemaVersion": 5, "runDefaults": {"meshConfig": {}, "simulationConfig": {}}}))
    return path


def _accepted_mesh(case: Path, *, hashes=None, status="accepted"):
    (case / "Mesh" / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
    (case / "mesh-quality-receipt.json").write_text(json.dumps(
        {"format": "air-mesh-quality/v1", "status": status, "failedChecks": 0, "meshSha256": "m" * 64}))
    if hashes is None:
        hashes = {key: row["contentHash"] for key, row in _manifest()["geometryExport"]["stlFiles"].items()}
    (case / "source-surface-mesh-source.json").write_text(json.dumps(
        {"format": "source-surface-mesh-source/v2", "exported_stl_hashes": hashes, "mesh_sha256": "m" * 64}))


def test_default_run_meshes_then_solves_with_the_same_manifest(stand_ins, project):
    result = air.run_air_case(project)
    assert result["status"] == "succeeded", result["failure"]
    session, = FakeSession.instances
    assert session.app == "api"
    mesh, solve = session.bodies
    assert mesh["meshOnly"] is True and mesh["stlFiles"]
    assert solve["solveOnly"] is True and solve["stlFiles"] == {}
    assert mesh["cfdrunJson"] == solve["cfdrunJson"]
    assert result["inputs"]["stages"] == ["mesh", "solve"]
    assert set(result["stages"]) >= {"export", "server", "mesh", "solve", "metrics"}
    assert result["metrics"]["fields"] == {"read": True}


def test_mesh_only_submits_one_mesh_job(stand_ins, project):
    result = air.run_air_case(project, stages=("mesh",))
    assert result["status"] == "succeeded", result["failure"]
    body, = FakeSession.instances[0].bodies
    assert body["meshOnly"] is True
    assert "solve" not in result["stages"] and result["inputs"]["stages"] == ["mesh"]
    # the legacy spelling still means the same
    assert air.run_air_case(project, solve=False)["inputs"]["stages"] == ["mesh"]


def test_solve_only_reuses_an_accepted_mesh_whose_stl_batch_matches(stand_ins, project):
    _accepted_mesh(project.parent)
    result = air.run_air_case(project, stages=("solve",))
    assert result["status"] == "succeeded", result["failure"]
    body, = FakeSession.instances[0].bodies
    assert body["solveOnly"] is True and body["meshOnly"] is False and body["stlFiles"] == {}
    assert "mesh" not in result["stages"]
    reuse = result["stages"]["meshReuse"]
    assert reuse["qualityReceipt"]["status"] == "accepted"
    assert reuse["stlBatch"] == {"basis": "source-surface-mesh-source.json", "files": 2, "match": True}
    assert result["stages"]["solve"]["returncode"] == 0


def test_the_solver_engine_goes_to_the_solve_job_only(stand_ins, project, monkeypatch):
    from backend.aircooling.runner import gpu_newton
    monkeypatch.setattr(gpu_newton, "configuration", lambda env=None: {"root": "r"})
    monkeypatch.setattr(gpu_newton, "identity", lambda env=None: {"engine": "gpuNewton", "commit": "c"})
    result = air.run_air_case(project, solver_engine="gpuNewton")
    assert result["status"] == "succeeded", result["failure"]
    mesh, solve = FakeSession.instances[0].bodies
    assert "solverEngine" not in mesh and solve["solverEngine"] == "gpuNewton"
    assert mesh["cfdrunJson"] == solve["cfdrunJson"]      # the engine is not part of the manifest
    assert result["inputs"]["solverEngine"] == "gpuNewton"
    assert result["stages"]["solve"]["solverEngine"] == "gpuNewton"
    assert result["identity"]["tools"]["solverEngine"] == {"engine": "gpuNewton", "commit": "c"}


def test_a_gpu_solve_starts_its_server_with_the_gpu_flag(stand_ins, project, monkeypatch):
    """Only a --gpu launch offers the engine; DCDTUNI_GPU_SOLVER_GPU names the cards, unset means any."""
    from backend.aircooling.runner import gpu_newton
    monkeypatch.setattr(gpu_newton, "configuration", lambda env=None: {"root": "r"})
    monkeypatch.setattr(gpu_newton, "identity", lambda env=None: {"engine": "gpuNewton", "commit": "c"})
    monkeypatch.delenv("DCDTUNI_GPU_SOLVER_GPU", raising=False)
    air.run_air_case(project, solver_engine="gpuNewton")
    assert FakeSession.instances[-1].gpu == ""
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER_GPU", "GPU-a,GPU-b")
    air.run_air_case(project, solver_engine="gpuNewton")
    assert FakeSession.instances[-1].gpu == "GPU-a,GPU-b"
    air.run_air_case(project, solver_engine="gpuNewton", stages=("mesh",))       # no solve, no GPU launch
    assert FakeSession.instances[-1].gpu is None
    air.run_air_case(project)
    assert FakeSession.instances[-1].gpu is None


def test_openfoam_stays_the_default_solver_engine(stand_ins, project):
    result = air.run_air_case(project)
    assert all("solverEngine" not in body for body in FakeSession.instances[0].bodies)
    assert result["inputs"]["solverEngine"] == "openfoam"
    assert result["stages"]["solve"]["solverEngine"] == "openfoam"
    assert "solverEngine" not in result["identity"]["tools"]


def test_an_unknown_or_unconfigured_solver_engine_is_refused_before_any_server(stand_ins, project, monkeypatch,
                                                                              tmp_path):
    with pytest.raises(ValueError, match="solver engine must be one of"):
        air.run_air_case(project, solver_engine="simpleFoam")
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER_ROOT", str(tmp_path / "no-solver-here"))
    with pytest.raises(FileNotFoundError, match="DCDTUNI_GPU_SOLVER_ROOT"):
        air.run_air_case(project, solver_engine="gpuNewton")
    assert FakeSession.instances == []


def test_solve_only_refuses_without_a_mesh_quality_record(stand_ins, project):
    result = air.run_air_case(project, stages=("solve",))
    assert result["status"] == "failed"
    assert result["failure"]["stage"] == "input"
    assert "mesh-quality-receipt" in result["failure"]["reason"]
    assert FakeSession.instances == []  # no server was started


def test_solve_only_solves_a_rejected_mesh_and_reports_the_verdict(stand_ins, project):
    """Owner rule (2026-10-22): mesh quality is reported, never required."""
    _accepted_mesh(project.parent, status="rejected")
    result = air.run_air_case(project, stages=("solve",))
    assert result["status"] == "succeeded", result.get("failure")
    assert result["stages"]["meshReuse"]["qualityReceipt"]["status"] == "rejected"
    assert result["stages"]["solve"]["returncode"] == 0


def test_solve_only_refuses_a_changed_stl_batch(stand_ins, project):
    _accepted_mesh(project.parent, hashes={"stl_000": "0" * 64, "stl_001": "1" * 64})
    result = air.run_air_case(project, stages=("solve",))
    assert result["failure"]["stage"] == "input"
    assert "STL" in result["failure"]["reason"] and "stl_000" in result["failure"]["reason"]
    assert FakeSession.instances == []


def test_solve_only_without_source_surfaces_compares_the_mesh_trisurface(stand_ins, project, monkeypatch):
    stand_ins.manifest = _manifest(source_surfaces=False)
    case = project.parent
    _accepted_mesh(case)
    (case / "source-surface-mesh-source.json").unlink()
    for name, text in STL.items():
        (case / "Mesh" / "constant" / "triSurface" / name).write_text(text)
    result = air.run_air_case(project, stages=("solve",))
    assert result["status"] == "succeeded", result["failure"]
    assert result["stages"]["meshReuse"]["stlBatch"]["basis"] == "Mesh/constant/triSurface"
    (case / "Mesh" / "constant" / "triSurface" / "merged_acu.stl").write_text("solid other\nendsolid other\n")
    assert air.run_air_case(project, stages=("solve",))["failure"]["stage"] == "input"


@pytest.mark.parametrize("stages", [("mesh", "solve"), ("solve",)])
def test_serial_solve_is_refused_at_input(stand_ins, project, stages):
    stand_ins.manifest = _manifest(parallel_solve=False)
    _accepted_mesh(project.parent)
    result = air.run_air_case(project, stages=stages)
    assert result["failure"]["stage"] == "input"
    assert "serial" in result["failure"]["reason"] and "allow_serial_solve" in result["failure"]["reason"]
    assert FakeSession.instances == []


def test_serial_solve_runs_when_explicitly_allowed_and_mesh_only_is_never_refused(stand_ins, project):
    stand_ins.manifest = _manifest(parallel_solve=False)
    assert air.run_air_case(project, stages=("mesh",))["status"] == "succeeded"
    result = air.run_air_case(project, allow_serial_solve=True)
    assert result["status"] == "succeeded", result["failure"]
    assert result["inputs"]["allowSerialSolve"] is True


def test_stage_selection_is_validated(project):
    with pytest.raises(ValueError):
        air.resolve_stages(("export",), solve=True)
    with pytest.raises(ValueError):
        air.resolve_stages((), solve=True)
    with pytest.raises(ValueError):
        air.resolve_stages(("solve",), solve=False)
    assert air.resolve_stages(("solve", "mesh"), solve=True) == ("mesh", "solve")
    assert air.resolve_stages(None, solve=False) == ("mesh",)


def test_solver_type_is_handed_to_the_editor_mode_switch(stand_ins, project):
    air.run_air_case(project, stages=("mesh",), solver_type="transient")
    call = stand_ins.calls[-1]
    assert call["options"] == {"solverType": "transient", "monitoring": air.DEFAULT_MONITORING}
    # never written straight into simulationConfig: setSimulationMode does that, with transient.airMode
    assert "solverType" not in call["project"]["runDefaults"]["simulationConfig"]
    air.run_air_case(project, stages=("mesh",), run_settings={"simulationConfig": {"solverType": "transient",
                                                                                  "endTime": 7}})
    call = stand_ins.calls[-1]
    assert call["options"]["solverType"] == "transient"
    assert call["project"]["runDefaults"]["simulationConfig"] == {"endTime": 7}
    result = air.run_air_case(project, stages=("mesh",), solver_type="steady",
                              run_settings={"simulationConfig": {"solverType": "transient"}})
    assert result["failure"]["stage"] == "input" and "solverType" in result["failure"]["reason"]
    with pytest.raises(ValueError):
        air.run_air_case(project, solver_type="implicit")


def test_mesher_identity_follows_the_engine(stand_ins, project):
    assert air.mesher_binary_name("dcCart") == "dcCartMesh"
    assert air.mesher_binary_name("snappy") == "snappyHexMesh"
    assert air.mesher_binary_name(None) == "dcCartMesh"
    stand_ins.manifest = _manifest(engine="snappy")
    result = air.run_air_case(project, stages=("mesh",))
    assert result["identity"]["tools"]["mesher"]["path"].endswith("snappyHexMesh")


def test_mesh_engines_are_the_schema_engines():
    from typing import get_args
    from backend.aircooling.runner.cfdrun_schema import MeshConfig
    literal = [arg for arg in get_args(MeshConfig.model_fields["meshEngine"].annotation) if get_args(arg)][0]
    assert set(air.mesh_engines()) == set(get_args(literal)) == {"dcCart", "snappy"}


RETIRED = "the meshgen engine was retired on 2026-10-26; choose dcCart or snappy"


@pytest.mark.parametrize("where", ["run_settings", "saved_project"])
def test_retired_meshgen_is_an_input_failure_before_any_server(stand_ins, project, where):
    run_settings = None
    if where == "run_settings":
        run_settings = {"meshConfig": {"meshEngine": "meshgen"}}
    else:
        project.write_text(json.dumps({"schemaVersion": 5, "runDefaults": {
            "meshConfig": {"meshEngine": "meshgen"}, "simulationConfig": {}}}))
    result = air.run_air_case(project, stages=("mesh",), run_settings=run_settings)
    assert result["status"] == "failed"
    assert result["failure"]["stage"] == "input"
    assert RETIRED in result["failure"]["reason"]
    assert stand_ins.calls == [] and FakeSession.instances == []
    with pytest.raises(ValueError, match=RETIRED):
        air.export_air_case(project, run_settings=run_settings)


def test_retired_meshgen_is_refused_as_a_cli_or_batch_option():
    with pytest.raises(ValueError, match=RETIRED):
        air.run_settings_from_options(mesh_engine="meshgen")
    assert air.run_settings_from_options(mesh_engine="snappy") == {"meshConfig": {"meshEngine": "snappy"}}


# ── result artifacts ────────────────────────────────────────────────────────

def test_artifacts_list_both_job_logs_and_the_server_log(stand_ins, project, monkeypatch):
    def start(self):
        self.log_path.write_text("server started\n")
    monkeypatch.setattr(FakeSession, "start", start)
    result = air.run_air_case(project)
    assert result["status"] == "succeeded", result["failure"]
    run_dir = Path(result["runDir"])
    artifacts = result["artifacts"]
    assert artifacts["meshJobLog"] == str(run_dir / "mesh-job.log")
    assert artifacts["solveJobLog"] == str(run_dir / "solve-job.log")
    assert artifacts["serverLog"] == str(run_dir / "server.log")
    assert all(Path(artifacts[key]).is_file() for key in ("meshJobLog", "solveJobLog", "serverLog"))


# ── solve monitoring: the 求解监控 panel ───────────────────────────────────────

def _mesh_boundary(case: Path, names):
    poly = case / "Mesh" / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    for name in ("points", "faces", "owner", "neighbour"):
        (poly / name).write_text("mock complete mesh")
    (poly / "boundary").write_text(
        f"{len(names)}\n(\n" + "".join(f"    {name}\n    {{\n        type patch;\n        nFaces 1;\n    }}\n"
                                      for name in names) + ")\n")


def test_the_api_ticks_the_acu_returns_and_run_settings_monitoring_replaces_the_ticks(stand_ins, project):
    air.run_air_case(project, stages=("mesh",))
    assert stand_ins.calls[-1]["options"]["monitoring"] == {"residuals": True, "acuReturnTemperature": "all"}
    air.run_air_case(project, stages=("mesh",), run_settings={"simulationConfig": {
        "endTime": 9, "monitoring": {"residuals": False, "acuReturnTemperature": ["ACU_1_Top_patch"]}}})
    call = stand_ins.calls[-1]
    assert call["options"]["monitoring"] == {"residuals": False, "acuReturnTemperature": ["ACU_1_Top_patch"]}
    # the panel's ticks are not a project field: the export reads them through the bridge only
    assert call["project"]["runDefaults"]["simulationConfig"] == {"endTime": 9}
    refused = air.run_air_case(project, stages=("mesh",),
                               run_settings={"simulationConfig": {"monitoring": {"probes": True}}})
    assert refused["failure"]["stage"] == "input" and "probes" in refused["failure"]["reason"]
    assert air.split_monitoring(None) == (None, {"residuals": True, "acuReturnTemperature": "all"})


def test_solve_only_offers_only_the_monitors_the_existing_mesh_has(stand_ins, project):
    _accepted_mesh(project.parent)
    _mesh_boundary(project.parent, ["ACU_1_Top_patch", "wall_patch"])
    result = air.run_air_case(project, stages=("solve",))
    assert result["status"] == "succeeded", result["failure"]
    assert stand_ins.calls[-1]["options"]["monitoring"]["meshPatches"] == ["ACU_1_Top_patch", "wall_patch"]
    # a full run meshes first: an old mesh in the case says nothing about the new one
    air.run_air_case(project)
    assert "meshPatches" not in stand_ins.calls[-1]["options"]["monitoring"]


class PanelRecorder(Recorder):
    """Exports what the panel offers: both bound returns, or those the given mesh has."""

    RETURNS = ("ACU_1_Top_patch", "ACU_buried_patch")

    def __call__(self, operation, project, project_path, *, timestamp=None, options=None, **_):
        reply = super().__call__(operation, project, project_path, timestamp=timestamp, options=options)
        mesh = ((options or {}).get("monitoring") or {}).get("meshPatches")
        manifest = json.loads(reply["cfdrunJson"])
        manifest["simulationConfig"]["monitoring"] = {
            "residuals": True, "serverTemperature": [],
            "acuReturnTemperature": [{"patch": name, "shortId": name[:5], "type": "return"}
                                     for name in self.RETURNS if mesh is None or name in mesh]}
        reply["cfdrunJson"] = json.dumps(manifest)
        return reply


def test_a_monitor_the_new_mesh_lacks_is_dropped_by_a_re_export_before_the_solve(stand_ins, project, monkeypatch):
    recorder = PanelRecorder()
    monkeypatch.setattr(air, "compile_project", recorder)
    _mesh_boundary(project.parent, ["ACU_1_Top_patch", "wall_patch"])  # the mesh the mesh job leaves
    result = air.run_air_case(project)
    assert result["status"] == "succeeded", result["failure"]
    mesh, solve = FakeSession.instances[0].bodies
    monitored = lambda body: [row["patch"] for row in
                              json.loads(body["cfdrunJson"])["simulationConfig"]["monitoring"]["acuReturnTemperature"]]
    assert monitored(mesh) == ["ACU_1_Top_patch", "ACU_buried_patch"]
    assert monitored(solve) == ["ACU_1_Top_patch"]
    assert recorder.calls[-1]["options"]["monitoring"]["meshPatches"] == ["ACU_1_Top_patch", "wall_patch"]
    assert result["stages"]["monitoring"]["notInMesh"] == ["ACU_buried_patch"]
    assert Path(result["artifacts"]["manifest"]).read_text() == mesh["cfdrunJson"]
    assert Path(result["artifacts"]["solveManifest"]).read_text() == solve["cfdrunJson"]
    # every monitored patch in the mesh: one export, the same manifest for both jobs
    FakeSession.instances = []
    _mesh_boundary(project.parent, ["ACU_1_Top_patch", "ACU_buried_patch"])
    again = air.run_air_case(project)
    mesh, solve = FakeSession.instances[0].bodies
    assert mesh["cfdrunJson"] == solve["cfdrunJson"] and "monitoring" not in again["stages"]


# ── solve-only of a hybrid project: the hybrid mesh source ─────────────────────

def _hybrid_manifest():
    manifest = _manifest()
    # what requires_hybrid_air_gate (the runner) reads: a liquid server with fan flow
    manifest["meshGeometry"] = {"racks": {"R1": {"servers": [{"sourceType": "liquidServer", "type": "fanServer"}]}}}
    return manifest


def test_mesh_reuse_of_a_hybrid_manifest_reads_the_hybrid_mesh_source(tmp_path):
    hashes = {key: row["contentHash"] for key, row in _manifest()["geometryExport"]["stlFiles"].items()}
    _accepted_mesh(tmp_path)
    (tmp_path / "source-surface-mesh-source.json").unlink()
    with pytest.raises(air.PipelineFailure, match="hybrid-mesh-source.json"):
        air.check_mesh_reuse(tmp_path, _hybrid_manifest())
    (tmp_path / "hybrid-mesh-source.json").write_text(json.dumps(
        {"format": "hybrid-mesh-source/v2", "exported_stl_hashes": hashes, "mesh_sha256": "m" * 64}))
    # the uploaded batch itself is gone: the server could not measure the liquid servers' faces
    with pytest.raises(air.PipelineFailure, match="air-inputs"):
        air.check_mesh_reuse(tmp_path, _hybrid_manifest())
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    for name, text in STL.items():
        (tri / name).write_text(text)
    reuse = air.check_mesh_reuse(tmp_path, _hybrid_manifest())
    assert reuse["stlBatch"] == {"basis": "hybrid-mesh-source.json", "files": 2, "match": True,
                                 "uploadedBatch": str(tri)}
    # the runner's predicate, or the API's own: a solve that carries hybridHeat is a hybrid solve
    assert air.check_mesh_reuse(tmp_path, _manifest(), hybrid=True)["stlBatch"]["basis"] == "hybrid-mesh-source.json"
    with pytest.raises(air.PipelineFailure, match="source-surface-mesh-source.json"):
        air.check_mesh_reuse(tmp_path, _manifest())
    (tmp_path / "hybrid-mesh-source.json").write_text(json.dumps(
        {"format": "hybrid-mesh-source/v2", "exported_stl_hashes": {**hashes, "stl_000": "0" * 64},
         "mesh_sha256": "m" * 64}))
    with pytest.raises(air.PipelineFailure, match="stl_000"):
        air.check_mesh_reuse(tmp_path, _hybrid_manifest())


@pytest.mark.parametrize("stages, stage", [(("mesh",), "mesh"), (("mesh", "solve"), "solve")])
def test_a_server_refusal_of_a_job_fails_that_stage(stand_ins, project, monkeypatch, stages, stage):
    from agent.scripted_api.server_session import ServerError
    original = FakeSession.submit_run

    def refuse(self, body):
        if (stage == "mesh") is bool(body.get("meshOnly")):
            raise ServerError(400, {"error": "geometryExport.stlFiles disagrees with actual grouped STL files"},
                              "/api/run-mesh")
        return original(self, body)
    monkeypatch.setattr(FakeSession, "submit_run", refuse)
    result = air.run_air_case(project, stages=stages)
    assert result["failure"]["stage"] == stage, result["failure"]
    assert "disagrees" in result["failure"]["reason"] and "traceback" not in result["failure"]
