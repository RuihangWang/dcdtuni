"""The in-process run session and the ``dceval`` front door, without a mesher or a solver.

The runner command is replaced by a small stand-in child that reads the manifest on stdin and prints what a
mesh or solve job prints, so these tests pin what is published into the case, how a job is started, followed
and stopped, and what its final status says.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    source = HERE / "direct_session.py"
    if source.is_file():  # beside its source, before an export has placed it in the package
        spec = importlib.util.spec_from_file_location("backend.api.direct_session", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module("backend.api.direct_session")


ds = _load()


@pytest.fixture(autouse=True)
def _modules(monkeypatch):
    monkeypatch.setitem(sys.modules, "backend.api.direct_session", ds)
    if (HERE / "dceval").is_dir():
        monkeypatch.syspath_prepend(str(HERE))


STL = {"merged_acu.stl": "solid acu\nendsolid acu\n", "merged_rack_frame.stl": "solid rack\nendsolid rack\n"}


def _sha(data) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _manifest(**extra):
    files = {f"stl_{i:03d}": {"path": name, "kind": "grouped_ascii", "contentHash": _sha(text)}
             for i, (name, text) in enumerate(sorted(STL.items()))}
    return {"schemaVersion": 4, "runId": "run_x", "sourceProject": {"name": "X.cfdproj"},
            "meshConfig": {"meshEngine": "dcCart", "parallelMesh": True, "meshProcess": 2},
            "simulationConfig": {"parallelSolve": True, "solveProcess": 2, "solverType": "steady", "endTime": 5},
            "geometryExport": {"stlFiles": files}, **extra}


def _stl_body():
    return {name: base64.b64encode(text.encode("utf-8")).decode("ascii") for name, text in STL.items()}


@pytest.fixture
def case(tmp_path):
    path = (tmp_path / "case").resolve()
    path.mkdir()
    return path


# ── 1. publication ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, safe", [
    ("Room.cfdrun", "Room.cfdrun"), ("Room", "Room.cfdrun"), ("../../etc/passwd.cfdrun", "passwd.cfdrun"),
    ("a b;c.cfdrun", "a_b_c.cfdrun"), ("", "project.cfdrun"), (None, "project.cfdrun"),
    ("..cfdrun", "project.cfdrun"), ("x.CFDRUN", "x.cfdrun"), ("__a__.cfdrun", "a.cfdrun"),
])
def test_manifest_filename_is_sanitized(raw, safe):
    assert ds._sanitize_cfdrun_filename(raw) == safe


def test_fresh_batch_is_published_with_artifacts_and_archived(case):
    text = json.dumps(_manifest())
    # what an earlier run left: another manifest, derived sidecars, surfaces of another batch, a kept file
    (case / "model").mkdir()
    (case / "model" / "Old.cfdrun").write_text("{}")
    (case / "model" / "bridge_bc.json").write_text("{}")
    (case / "model" / "heat-ledger.json").write_text("{}")
    tri = case / "Mesh" / "constant" / "triSurface"
    tri.mkdir(parents=True)
    (tri / "stale.stl").write_text("solid stale\nendsolid stale\n")
    (tri / "stale.eMesh").write_text("edges")
    (tri / "notes.txt").write_text("kept")
    input_id = "12345678-1234-4234-8234-123456789abc"
    request = {"cfdrunJson": text, "cfdrunFilename": "X.cfdrun", "stlFiles": _stl_body(), "meshOnly": True,
               "solveOnly": False, "caseDir": str(case)}
    request_bytes = json.dumps(request).encode("utf-8")

    snapshot = ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body(), original_input_id=input_id,
                                               original_request=(request_bytes, request))

    expected = _manifest()
    expected["artifacts"] = {"caseDir": str(case), "modelDir": str(case / "model"),
                             "triSurfaceDir": str(tri), "openfoamCaseDir": str(case / "Mesh"), "logs": {}}
    payload = json.dumps(expected, ensure_ascii=False, indent=2).encode("utf-8")
    published = case / "model" / "X.cfdrun"
    assert published.read_bytes() == payload == snapshot.read_bytes()
    assert snapshot.sha256 == _sha(payload) and snapshot.logical_path == published
    assert sorted(path.name for path in (case / "model").iterdir()) == ["X.cfdrun"]
    assert {path.name: path.read_text() for path in tri.iterdir()} == {**STL, "notes.txt": "kept"}
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
    assert snapshot.stl_batch_sha256 == surface_batch_sha256(case)

    archive = case / "log" / "air-inputs" / input_id
    receipt = json.loads((archive / "INPUT.json").read_text())
    assert receipt["format"] == ds.ORIGINAL_INPUT_FORMAT and receipt["format"].endswith(".air.original-input/v1")
    assert (receipt["inputId"], receipt["jobId"]) == (input_id, "12345678")
    assert receipt["sourceProject"] == {"name": "X.cfdproj"}
    assert (receipt["frontendManifest"], receipt["publishedManifest"]) == ("frontend/X.cfdrun", "model/X.cfdrun")
    assert receipt["networkRequestBodyCaptured"] is True
    assert receipt["requestBody"] == {"captured": True, "bytes": len(request_bytes), "sha256": _sha(request_bytes),
                                      "path": "request.json"}
    rows = {row["path"]: row for row in receipt["files"]}
    assert set(rows) == {"frontend/X.cfdrun", "model/X.cfdrun", "request.json",
                         *("Mesh/constant/triSurface/" + name for name in STL)}
    for relative, row in rows.items():
        data = (archive / relative).read_bytes()
        assert (row["bytes"], row["sha256"]) == (len(data), _sha(data))
    assert (archive / "frontend" / "X.cfdrun").read_text() == text
    assert (archive / "model" / "X.cfdrun").read_bytes() == payload
    assert (archive / "request.json").read_bytes() == request_bytes
    assert not [path for path in case.iterdir() if "-upload-" in path.name]


def test_request_with_other_keys_is_not_archived(case):
    text = json.dumps(_manifest())
    request = {"cfdrunJson": text, "stlFiles": _stl_body(), "solverEngine": "openfoam"}
    input_id = "12345678-1234-4234-8234-123456789abc"
    ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body(), original_input_id=input_id,
                                    original_request=(b"{}", request))
    archive = case / "log" / "air-inputs" / input_id
    receipt = json.loads((archive / "INPUT.json").read_text())
    assert receipt["requestBody"] == {"captured": False, "bytes": 2, "sha256": _sha(b"{}"),
                                      "reason": "extra-fields-not-retained"}
    assert not (archive / "request.json").exists()


def test_solve_reuses_the_published_manifest_unchanged(case):
    text = json.dumps(_manifest())
    first = ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body())
    before = (case / "model" / "X.cfdrun").stat().st_mtime_ns
    again = ds._publish_case_input_snapshot(case, "X.cfdrun", text, {})
    assert again.sha256 == first.sha256 and again.stl_batch_sha256 == first.stl_batch_sha256
    # a solve sends the same manifest text and no STL: the manifest is rewritten with the same bytes
    assert (case / "model" / "X.cfdrun").read_bytes() == first.read_bytes()
    assert sorted(path.name for path in (case / "Mesh" / "constant" / "triSurface").iterdir()) == sorted(STL)
    existing = ds._publish_case_input_snapshot(case, None, None, None)
    assert existing.sha256 == first.sha256 and (case / "model" / "X.cfdrun").stat().st_mtime_ns >= before


@pytest.mark.parametrize("stl_files, message", [
    ({"../a.stl": "AAAA"}, "Invalid STL filename"), ({"a.txt": "AAAA"}, "Invalid STL filename"),
    ({"a.stl": "not base64!"}, "Invalid base64"), ({"a.stl": 3}, "must be base64 text"), (["a.stl"], "must be an object"),
])
def test_invalid_stl_uploads_are_refused(stl_files, message):
    with pytest.raises(ValueError, match=message):
        ds._decode_stl_uploads(stl_files)


def test_failed_publication_leaves_the_case_as_it_was(case):
    text = json.dumps(_manifest())
    ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body())
    before = {path.relative_to(case).as_posix(): path.read_bytes() for path in case.rglob("*") if path.is_file()}
    wrong = {**_stl_body(), "merged_acu.stl": base64.b64encode(b"solid other\nendsolid other\n").decode()}
    with pytest.raises(ValueError, match="differs from its export-time content hash"):
        ds._publish_case_input_snapshot(case, "X.cfdrun", text, wrong,
                                        original_input_id="12345678-1234-4234-8234-123456789abc")
    after = {path.relative_to(case).as_posix(): path.read_bytes() for path in case.rglob("*") if path.is_file()}
    assert after == before


def test_hybrid_heat_is_compiled_by_the_injected_manager(case):
    text = json.dumps(_manifest())
    calls = []

    class Manager:
        def compile_hybrid_air_heat(self, run_id, *, project, air_cfdrun, tri_surface_dir, sample_index, case_dir):
            calls.append((run_id, project, sorted(path.name for path in Path(tri_surface_dir).iterdir()),
                          sample_index, case_dir, air_cfdrun["artifacts"]["caseDir"]))
            return {**air_cfdrun, "liquidHeat": {"rack": 1200.0}}, {"liquid_run_id": run_id, "sample_index": 4}

    heat = {"project": {"id": "p"}, "liquidRunId": "run-1", "sampleIndex": 4}
    snapshot = ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body(), hybrid_heat=heat,
                                               liquid_manager=lambda: Manager())
    assert calls == [("run-1", {"id": "p"}, sorted(STL), 4, case, str(case))]
    published = (case / "model" / "X.cfdrun").read_bytes()
    assert published == snapshot.read_bytes() and published.endswith(b"}\n")
    assert json.loads(published)["liquidHeat"] == {"rack": 1200.0}
    assert json.loads((case / "model" / "heat-ledger.json").read_text()) == {"liquid_run_id": "run-1",
                                                                             "sample_index": 4}
    with pytest.raises(ValueError, match="liquid run manager"):
        ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body(), hybrid_heat=heat)
    with pytest.raises(ValueError, match="hybridHeat needs"):
        ds._publish_case_input_snapshot(case, "X.cfdrun", text, _stl_body(), hybrid_heat={**heat, "sampleIndex": "4"},
                                        liquid_manager=lambda: Manager())


def test_archived_batch_is_found_by_content(case):
    manifest = _manifest()
    assert ds._archived_stl_batch_dir(case, manifest) is None
    for name, content in (("old", {**STL, "merged_acu.stl": "solid changed\nendsolid changed\n"}), ("match", STL)):
        tri = case / "log" / "air-inputs" / name / "Mesh" / "constant" / "triSurface"
        tri.mkdir(parents=True)
        for filename, text in content.items():
            (tri / filename).write_text(text)
    assert ds._archived_stl_batch_dir(case, manifest) == case / "log/air-inputs/match/Mesh/constant/triSurface"
    assert ds._archived_stl_batch_dir(case, {"geometryExport": {"stlFiles": {}}}) is None


# ── 2. what a finished job reports ──────────────────────────────────────────

MESH_LOG_OK = """\
Create mesh for time = 0
Mesh OK.
2026-01-01 10:00:00 | INFO | Step 7: checkMesh (final quality check)
Checking topology...
    Boundary definition OK.
    Number of regions: 1 (OK).
Checking geometry...
    Max skewness = 1.9 OK.
Mesh OK.
End""".splitlines()

MESH_LOG_CONCAVE_ONLY = """\
Failed 4 mesh checks.
2026-01-01 10:00:00 | INFO | Step 7: checkMesh (final quality check)
Checking topology...
    Boundary definition OK.
Checking geometry...
 ***Concave cells (using face planes) found, number of cells: 12
Failed 1 mesh checks.
End""".splitlines()

MESH_LOG_FAILED = """\
2026-01-01 10:00:00 | INFO | Step 7: checkMesh (final quality check)
Checking topology...
Checking geometry...
 ***Concave cells (using face planes) found, number of cells: 12
 ***Error in face tets: 3 faces with low quality or negative volume decomposition tets.
Failed 2 mesh checks.
End""".splitlines()


def test_mesh_quality_is_read_from_the_final_check():
    ok = ds._summarize_mesh_run_quality(MESH_LOG_OK, 0)
    assert (ok["checkMeshOk"], ok["failedChecks"], ok["meshWarnings"]) == (True, 0, [])
    concave = ds._summarize_mesh_run_quality(MESH_LOG_CONCAVE_ONLY, 0)
    assert (concave["checkMeshOk"], concave["failedChecks"]) == (True, 0)
    failed = ds._summarize_mesh_run_quality(MESH_LOG_FAILED, 0)
    assert (failed["checkMeshOk"], failed["failedChecks"]) == (False, 1)
    assert "bad_face_tets" in [row["code"] for row in failed["meshWarnings"]]
    # a mesh job that exited non-zero is never accepted, whatever its last check printed
    assert ds._summarize_mesh_run_quality(MESH_LOG_OK, 1)["checkMeshOk"] is False


def test_mesh_job_without_a_final_verdict_is_not_accepted():
    missing = ds._summarize_mesh_run_quality(["Mesh OK."], 0)
    assert missing["checkMeshOk"] is False and missing["meshWarnings"][0]["code"] == "missing_final_checkmesh"
    cut = ds._summarize_mesh_run_quality(["checkMesh (final quality check)", "Checking topology..."], 0)
    assert cut["checkMeshOk"] is False and cut["meshWarnings"][0]["code"] == "incomplete_checkmesh"


def test_solve_reports_a_mesh_refusal_only_when_the_runner_says_so():
    refusal = ["Traceback (most recent call last):",
               "RuntimeError: air solve requires accepted strict mesh quality: status=rejected"]
    assert ds._summarize_solve_mesh_quality_rejection(refusal, 1)["checkMeshOk"] is False
    assert ds._summarize_solve_mesh_quality_rejection(refusal, 0) is None
    assert ds._summarize_solve_mesh_quality_rejection(["FOAM FATAL ERROR: boundary"], 1) is None


def _solve_run(case, sha, *, verdict="CONVERGED", sensors=True, energy=True, receipt_sha=None):
    run = case / "log" / "2026-01-01-10-00-00_X"
    run.mkdir(parents=True)
    (run / "log.solver").write_text("End\n")
    (run / "air-run-execution-receipt.json").write_text(json.dumps(
        {"format": "air-run-execution-receipt/v1", "status": "accepted",
         "result": {"cfdrunSha256": receipt_sha or sha, "runRelativePath": "log/2026-01-01-10-00-00_X"}}))
    report = {"verdict": verdict, "rule": "steady return air decides; residuals are diagnostics",
              "log": str(run / "log.solver"),
              "residuals": {"passed": False, "reasons": ["residual of U above its target"]},
              "sensors": {"passed": sensors, "status": "STEADY" if sensors else "DRIFTING",
                          "reasons": [] if sensors else ["return air still drifting"]},
              "energy": {"passed": energy, "status": "CLOSED" if energy else "OPEN",
                         "reasons": [] if energy else ["heat balance open"]}}
    (run / "convergence.json").write_text(json.dumps(report))
    return run


def test_solve_assessment_relays_the_runner_verdict(case):
    sha = "a" * 64
    run = _solve_run(case, sha)
    found = ds._solve_job_assessment(case_dir=case, run_dir=run, cfdrun_sha256=sha)
    assert (found["verdict"], found["mode"], found["runDir"]) == ("CONVERGED", "steady", str(run))
    assert found["reportSha256"] == _sha((run / "convergence.json").read_bytes())
    assert found["reasons"] == ["residual of U above its target"] and found["format"] == "air-solve-assessment/v1"


@pytest.mark.parametrize("kwargs, verdict, reason", [
    (dict(verdict="NOT_CONVERGED", sensors=False), "NOT_CONVERGED", "return air still drifting"),
    (dict(verdict="NOT_CONVERGED", energy=False), "NOT_CONVERGED", "heat balance open"),
    (dict(verdict="CONVERGED", sensors=False), "UNKNOWN", "inconsistent steady convergence report"),
    (dict(receipt_sha="b" * 64), "UNKNOWN", "execution receipt does not match"),
])
def test_solve_assessment_checks_the_report(case, kwargs, verdict, reason):
    run = _solve_run(case, "a" * 64, **kwargs)
    found = ds._solve_job_assessment(case_dir=case, run_dir=run, cfdrun_sha256="a" * 64)
    assert found["verdict"] == verdict and reason in found["reasons"][0]


def test_solve_assessment_needs_a_run_directory_of_the_case(case, tmp_path):
    assert ds._solve_job_assessment(case_dir=case, run_dir=None, cfdrun_sha256="a" * 64)["reasons"] == [
        "this job did not publish a solve run directory"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    found = ds._solve_job_assessment(case_dir=case, run_dir=elsewhere, cfdrun_sha256="a" * 64)
    assert found["verdict"] == "UNKNOWN" and "does not belong to this job's case" in found["reasons"][0]


# ── 3. the session's jobs, with a stand-in for the runner ───────────────────

STAND_IN = r"""
import hashlib, json, os, subprocess, sys, time
mode, case = sys.argv[1], sys.argv[2]
sha = hashlib.sha256(sys.stdin.buffer.read()).hexdigest()
print("stand-in manifest", sha)
print("stand-in cwd", os.getcwd())
print("stand-in env", json.dumps({k: os.environ.get(k) for k in ("PYTHONUNBUFFERED", "FOAM_MPI", "UCX_TLS")}))
if mode == "mesh":
    print("2026-01-01 10:00:00 | INFO |   cells: 4321")
    print("checkMesh (final quality check)")
    print("Checking topology...")
    print("Mesh OK.")
elif mode == "solve":
    run = os.path.join(case, "log", "2026-01-01-10-00-00_X")
    os.makedirs(run)
    log = os.path.join(run, "log.solver")
    open(log, "w").write("End\n")
    json.dump({"format": "air-run-execution-receipt/v1", "status": "accepted",
               "result": {"cfdrunSha256": sha, "runRelativePath": "log/2026-01-01-10-00-00_X"}},
              open(os.path.join(run, "air-run-execution-receipt.json"), "w"))
    json.dump({"verdict": "CONVERGED", "rule": "steady return air decides; residuals are diagnostics", "log": log,
               "residuals": {"passed": True, "reasons": []},
               "sensors": {"passed": True, "status": "STEADY", "reasons": []}},
              open(os.path.join(run, "convergence.json"), "w"))
    print("Created run directory: " + run)
elif mode == "refused":
    print("RuntimeError: air solve requires accepted strict mesh quality: status=rejected")
    sys.exit(3)
elif mode == "hang":
    # a step in a session of its own, as the runner starts its MPI steps
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    print("stand-in step", child.pid, flush=True)
    time.sleep(600)
"""


@pytest.fixture
def stand_in(monkeypatch):
    """Replace the runner command; ``calls`` records what the real command would have been."""
    calls = []
    real = ds.build_run_command
    state = {"mode": None}

    def build(cfdrun_path, cfdrun_sha256, stl_batch_sha256, **options):
        calls.append({"command": real(cfdrun_path, cfdrun_sha256, stl_batch_sha256, **options), **options})
        mode = state["mode"] or ("mesh" if options["mesh_only"] else "solve")
        return [sys.executable, "-c", STAND_IN, mode, str(Path(cfdrun_path).parent.parent)]

    monkeypatch.setattr(ds, "build_run_command", build)
    return calls, state


def _mesh_body():
    return {"cfdrunJson": json.dumps(_manifest()), "cfdrunFilename": "X.cfdrun", "stlFiles": _stl_body(),
            "meshOnly": True, "solveOnly": False, "meshPreparationElapsedSeconds": 0.25}


def _solve_body(**extra):
    return {"cfdrunJson": json.dumps(_manifest()), "cfdrunFilename": "X.cfdrun", "stlFiles": {},
            "meshOnly": False, "solveOnly": True, **extra}


def test_session_surface_matches_what_the_air_run_uses(case, tmp_path):
    session = ds.DirectSession(case, tmp_path, app="api", gpu=None)
    assert session.port == 0 and session.stop_report == {} and session.startup_seconds is None
    assert all(isinstance(part, str) for part in session.command)
    session.log_path = tmp_path / "server.log"
    session.start()
    assert session.startup_seconds is not None and session.log_path.is_file()
    session.stop()
    assert session.stop_report == {"jobsAborted": [], "liquidRunsCancelled": []}
    assert issubclass(ds.ServerError, RuntimeError)
    error = ds.ServerError(409, {"code": "case_busy", "error": "Case is busy"}, "/api/run-mesh")
    assert (error.status, error.body["code"], error.path) == (409, "case_busy", "/api/run-mesh")


def test_mesh_job_runs_and_reports(case, tmp_path, stand_in):
    calls, _ = stand_in
    with ds.DirectSession(case, tmp_path) as session:
        reply = session.submit_run(_mesh_body())
        status = session.wait_job(reply["jobId"], timeout=60)
        lines, final = session.job_log(reply["jobId"])
    published = (case / "model" / "X.cfdrun").read_bytes()
    assert reply == {"jobId": reply["jobId"], "caseDir": str(case), "cfdrunSha256": _sha(published)}
    assert len(reply["jobId"]) == 8
    assert (status["done"], status["returncode"], status["timedOut"]) == (True, 0, False)
    assert (status["checkMeshOk"], status["failedChecks"], status["meshWarnings"]) == (True, 0, [])
    assert status["cancelled"] is False and status["solveAssessment"] is None
    # the child received the published manifest on stdin, ran in the case, with the run environment
    assert f"stand-in manifest {reply['cfdrunSha256']}" in lines and f"stand-in cwd {case}" in lines
    env = json.loads(next(line for line in lines if line.startswith("stand-in env "))[len("stand-in env "):])
    assert env == {"PYTHONUNBUFFERED": "1", "FOAM_MPI": "sys-mpich", "UCX_TLS": os.environ.get("UCX_TLS") or "self,sm"}
    assert final["done"] is True and final["returncode"] == 0 and final["checkMeshOk"] is True
    # the mesh's source batch is on record, and the marker a viewer opens the mesh with
    archive, = (case / "log" / "air-inputs").iterdir()
    assert archive.name.startswith(reply["jobId"])
    assert json.loads((case / "Mesh" / "mesh-source-batch.json").read_text()) == {"inputId": archive.name}
    assert (case / "Mesh" / "case.foam").is_file()
    assert json.loads((archive / "request.json").read_text())["caseDir"] == str(case)
    # the command the runner would have been started with
    command = calls[0]["command"]
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
    assert command[:9] == [sys.executable, "-m", "backend.aircooling.runner.run_cfd",
                           "--from-cfdrun", str(case / "model" / "X.cfdrun"), "--cfdrun-stdin",
                           "--expected-cfdrun-sha256", reply["cfdrunSha256"], "--expected-stl-batch-sha256"]
    assert command[9] == surface_batch_sha256(case) and command[10] == "--mesh-only"
    assert command[11::2] == ["--mesh-started-monotonic", "--mesh-deadline-monotonic"] and len(command) == 15
    started, deadline = float(command[12]), float(command[14])
    assert 0 < deadline - started <= 300 - 0.25 + 1e-6      # the export time is charged to the mesh budget


def test_solve_job_reports_the_assessment(case, tmp_path, stand_in):
    calls, _ = stand_in
    with ds.DirectSession(case, tmp_path) as session:
        mesh = session.submit_run(_mesh_body())
        session.wait_job(mesh["jobId"], timeout=60)
        reply = session.submit_run(_solve_body())
        status = session.wait_job(reply["jobId"], timeout=60)
        _, final = session.job_log(reply["jobId"])
    assert reply["cfdrunSha256"] == mesh["cfdrunSha256"]
    assert status["returncode"] == 0 and status["checkMeshOk"] is None
    assessment = status["solveAssessment"]
    assert assessment["verdict"] == "CONVERGED" and assessment["cfdrunSha256"] == reply["cfdrunSha256"]
    assert assessment["runDir"] == str(case / "log" / "2026-01-01-10-00-00_X")
    assert final["solveAssessment"] == assessment
    command = calls[1]["command"]
    assert command[10:] == ["--solve-only"]                  # no mesh budget, the default solver
    assert len(list((case / "log" / "air-inputs").iterdir())) == 1  # a solve archives nothing


def test_failed_solve_carries_the_mesh_refusal(case, tmp_path, stand_in):
    _, state = stand_in
    with ds.DirectSession(case, tmp_path) as session:
        session.wait_job(session.submit_run(_mesh_body())["jobId"], timeout=60)
        state["mode"] = "refused"
        status = session.wait_job(session.submit_run(_solve_body())["jobId"], timeout=60)
    assert status["returncode"] == 3 and status["checkMeshOk"] is False and status["solveAssessment"] is None


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


def test_job_past_its_limit_is_stopped_with_its_whole_tree(case, tmp_path, stand_in):
    _, state = stand_in
    state["mode"] = "hang"
    (case / "mesh-quality-receipt.json").write_text("{}")
    with ds.DirectSession(case, tmp_path) as session:
        reply = session.submit_run(_mesh_body())
        deadline = time.monotonic() + 30
        step = None
        while step is None and time.monotonic() < deadline:
            lines, final = session.job_log(reply["jobId"])
            step = next((int(line.split()[-1]) for line in lines if line.startswith("stand-in step ")), None)
            time.sleep(0.05)
        assert step is not None and final is None and _alive(step)
        status = session.wait_job(reply["jobId"], timeout=0.2, poll=0.05)
    assert status["timedOut"] is True and status["done"] is True
    assert (status["returncode"], status["cancelled"], status["cancelRequested"]) == (-15, True, True)
    assert status["checkMeshOk"] is False and status["abortReply"]["cancelRequested"] is True
    assert not _alive(step)                                   # the step in its own session is gone too
    assert not (case / "mesh-quality-receipt.json").exists()  # a cancelled mesh job leaves no quality record
    assert not (case / "Mesh" / "case.foam").exists()
    lines, final = session.job_log(reply["jobId"])
    assert any("aborted" in line for line in lines) and final["cancelled"] is True


def test_stopping_the_session_stops_its_unfinished_job(case, tmp_path, stand_in):
    _, state = stand_in
    state["mode"] = "hang"
    session = ds.DirectSession(case, tmp_path)
    session.start()
    reply = session.submit_run(_mesh_body())
    session.stop()
    assert session.stop_report["jobsAborted"] == [reply["jobId"]]
    assert session.job_status(reply["jobId"])["cancelled"] is True


@pytest.mark.parametrize("change, status, code", [
    ({"aiInit": {"model": "m"}}, 400, "ai_init_unavailable"),
    ({"solverEngine": "other"}, 400, "solver_engine_invalid"),
    ({"patchUpdate": True}, 400, "patch_update_unavailable"),
    ({"cfdrunJson": "{not json"}, 400, None),
    ({"cfdrunJson": 5}, 400, None),
])
def test_refused_bodies_raise_the_server_error(case, tmp_path, stand_in, change, status, code):
    with ds.DirectSession(case, tmp_path) as session:
        with pytest.raises(ds.ServerError) as refusal:
            session.submit_run(_solve_body(**change))
    assert refusal.value.status == status and refusal.value.body.get("code") == code
    assert refusal.value.path == "/api/run-mesh" and not session.job_ids


def test_busy_case_is_refused(case, tmp_path, stand_in):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock
    with ds.DirectSession(case, tmp_path) as session, case_execution_lock(case):
        with pytest.raises(ds.ServerError) as refusal:
            session.submit_run(_mesh_body())
    assert (refusal.value.status, refusal.value.body["code"]) == (409, "case_busy")


def test_gpu_solver_needs_no_launcher_flag_but_its_packages(case, tmp_path, stand_in, monkeypatch):
    from backend.aircooling.runner import gpu_newton
    calls, _ = stand_in
    monkeypatch.delenv(gpu_newton.ENABLE_ENV, raising=False)
    asked = []
    monkeypatch.setattr(gpu_newton, "configuration", lambda env=None: asked.append(env) or {"python": Path(sys.executable)})
    monkeypatch.setattr(ds, "missing_gpu_solver_modules", lambda python=None: ["warp"])
    with ds.DirectSession(case, tmp_path, gpu="GPU-aaaa") as session:
        session.wait_job(session.submit_run(_mesh_body())["jobId"], timeout=60)
        with pytest.raises(ds.ServerError, match="missing Python packages: warp") as refusal:
            session.submit_run(_solve_body(solverEngine=gpu_newton.GPU_NEWTON))
        assert refusal.value.body["code"] == "solver_engine_invalid"
        monkeypatch.setattr(ds, "missing_gpu_solver_modules", lambda python=None: [])
        reply = session.submit_run(_solve_body(solverEngine=gpu_newton.GPU_NEWTON))
        session.wait_job(reply["jobId"], timeout=60)
    assert calls[-1]["command"][10:] == ["--solve-only", "--solver-engine", "gpuNewton"]
    assert asked[-1][ds.GPU_ENV] == "GPU-aaaa" and asked[-1][gpu_newton.ENABLE_ENV] == "1"


def test_command_of_a_combined_job():
    command = ds.build_run_command(Path("/c/model/X.cfdrun"), "a" * 64, "b" * 64, mesh_only=False, solve_only=False,
                                   solver_engine="openfoam", mesh_budget_args=["--mesh-started-monotonic", "1"])
    assert command[3:] == ["--from-cfdrun", "/c/model/X.cfdrun", "--cfdrun-stdin", "--expected-cfdrun-sha256",
                           "a" * 64, "--expected-stl-batch-sha256", "b" * 64, "--mesh-started-monotonic", "1"]


# ── the environment of a run ────────────────────────────────────────────────

def test_run_environment_takes_openfoam_from_this_interpreter():
    from backend.aircooling.runner import gpu_newton
    base = {"PATH": "/usr/lib/openfoam2406/bin:/usr/bin::/opt/OpenFOAM/x:/usr/lib/x86_64/openmpi/bin:/bin",
            "LD_LIBRARY_PATH": "/usr/lib/openfoam2406/lib:/opt/lib", "PYTHONPATH": "/elsewhere",
            ds.GPU_ENV: "GPU-kept", gpu_newton.ENABLE_ENV: "0", "HOME": "/home/u"}
    env = ds.run_environment(base)
    prefix = sys.prefix
    assert env["PATH"] == f"{prefix}/bin:/usr/bin:/bin" and env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert {key: env[key] for key in ("WM_PROJECT_DIR", "CONDA_PREFIX", "MPI_ARCH_PATH")} == dict.fromkeys(
        ("WM_PROJECT_DIR", "CONDA_PREFIX", "MPI_ARCH_PATH"), prefix)
    assert (env["FOAM_LIBBIN"], env["FOAM_APPBIN"], env["FOAM_ETC"]) == (f"{prefix}/lib", f"{prefix}/bin",
                                                                        f"{prefix}/etc")
    assert env["FOAM_MPI"] == "sys-mpich" and env["UCX_TLS"] == "self,sm" and env["PYTHONUNBUFFERED"] == "1"
    src, inherited = env["PYTHONPATH"].split(":")
    assert inherited == "/elsewhere" and (Path(src) / "backend" / "api" / "identity.py").is_file()
    assert env["HOME"] == "/home/u" and base["PATH"].startswith("/usr/lib/openfoam2406")   # the base is not changed
    # the GPU solver's variables: untouched unless the session is given cards
    assert env[ds.GPU_ENV] == "GPU-kept" and env[gpu_newton.ENABLE_ENV] == "0"
    every = ds.run_environment(base, gpu="")
    assert ds.GPU_ENV not in every and every[gpu_newton.ENABLE_ENV] == "1"
    named = ds.run_environment({**base, "UCX_TLS": "tcp"}, gpu="GPU-aaaa,GPU-bbbb")
    assert named[ds.GPU_ENV] == "GPU-aaaa,GPU-bbbb" and named["UCX_TLS"] == "tcp"
    assert "LD_LIBRARY_PATH" not in ds.run_environment({"PATH": "/bin"})


def test_cards_are_handed_over_as_uuids():
    cards = [("0", "GPU-aaaa"), ("1", "GPU-bbbb"), ("2", "GPU-cccc")]
    assert ds.resolve_gpus([1, "0"], cards) == ["GPU-bbbb", "GPU-aaaa"]
    assert ds.resolve_gpus("2, GPU-dddd,2", cards) == ["GPU-cccc", "GPU-dddd"]
    assert ds.resolve_gpus(0, cards) == ["GPU-aaaa"] and ds.resolve_gpus(None, cards) == []
    assert ds.resolve_gpus("GPU-eeee") == ["GPU-eeee"]          # a UUID needs no nvidia-smi
    with pytest.raises(ValueError, match="no such card"):
        ds.resolve_gpus([7], cards)
    for bad in ("all", "0;1", [True], [-1], ""):
        with pytest.raises(ValueError, match="index or GPU-"):
            ds.resolve_gpus(bad, cards)


# ── 4. liquid run requests ──────────────────────────────────────────────────

class LiquidRuns:
    def __init__(self):
        self.calls = []
        self.fail = None

    def _do(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.fail is not None:
            raise self.fail
        return {"run_id": "r1", "status": {"create": "ready", "operate": "running"}.get(name, "completed"),
                "frame": {"seq": 2}, "values": (1, 2)}

    def create(self, liquidrun, **kwargs):
        return self._do("create", liquidrun, **kwargs)

    def operate(self, run_id, operation, **kwargs):
        return self._do("operate", run_id, operation, **kwargs)

    def get(self, run_id):
        return self._do("get", run_id)

    def result(self, run_id):
        return self._do("result", run_id)

    def loaded_runs(self):
        return {"r1": {"status": "running", "read_only": False}, "r0": {"status": "completed", "read_only": False}}


def test_liquid_requests_reach_the_session_manager(case, tmp_path):
    runs = LiquidRuns()
    session = ds.DirectSession(case, tmp_path, liquid_manager=runs)
    body = {"liquidrun": {"schemaVersion": 2}, "controllers": [{"id": "c"}]}
    created = session.call("POST", "/api/liquid/runs", body, timeout=900, expect=(201,))
    assert created["status"] == "ready" and created["values"] == [1, 2]      # a plain JSON document
    assert runs.calls[-1] == ("create", ({"schemaVersion": 2},), {"case_dir": case, "controllers": [{"id": "c"}],
                                                                  "project_revision": None, "checkpoint": None})
    assert session.call("POST", "/api/liquid/runs/r1/start", {}, timeout=120)["status"] == "running"
    assert runs.calls[-1] == ("operate", ("r1", "start"), {"speed": None})
    assert session.call("GET", "/api/liquid/runs/r1", timeout=120)["frame"] == {"seq": 2}
    assert runs.calls[-1] == ("get", ("r1",), {})
    session.call("GET", "/api/liquid/runs/r1/result", timeout=600)
    assert runs.calls[-1] == ("result", ("r1",), {})
    session.call("POST", "/api/liquid/runs/r1/cancel", {})
    assert runs.calls[-1] == ("operate", ("r1", "cancel"), {"speed": None})
    # the session's own manager also serves a hybrid solve, and stop() cancels what still runs
    assert session.liquid_manager() is runs
    session.stop()
    assert session.stop_report["liquidRunsCancelled"] == ["r1"] and runs.calls[-1][1] == ("r1", "cancel")


def test_liquid_refusals_carry_the_server_status(case, tmp_path):
    manager = pytest.importorskip("backend.liquidcooling.run_manager")
    runs = LiquidRuns()
    session = ds.DirectSession(case, tmp_path, liquid_manager=runs)
    for failure, status, text in ((manager.RunNotFoundError("r9"), 404, "liquid run 'r9' was not found"),
                                  (manager.RunConflictError("read-only"), 409, "read-only"),
                                  (manager.RunInputError("bad input"), 400, "bad input"),
                                  (RuntimeError("No CUDA GPUs are available"), 500, "No CUDA GPUs are available")):
        runs.fail = failure
        with pytest.raises(ds.ServerError) as refusal:
            session.call("GET", "/api/liquid/runs/r9")
        assert (refusal.value.status, refusal.value.body) == (status, {"error": text})
    runs.fail = None
    for method, path, payload, status in (("POST", "/api/liquid/runs", {"liquidrun": "text"}, 400),
                                          ("POST", "/api/liquid/runs", {"liquidrun": {}, "controllers": {}}, 400),
                                          ("POST", "/api/liquid/runs/r1/explode", {}, 404),
                                          ("GET", "/api/liquid/runs", None, 404),
                                          ("GET", "/api/server-info", None, 404),
                                          ("POST", "/api/liquid/runs/r1/start", [], 400)):
        with pytest.raises(ds.ServerError) as refusal:
            session.call(method, path, payload)
        assert refusal.value.status == status, (method, path)
    # a create answers 201, so a caller expecting 200 is told
    with pytest.raises(ds.ServerError) as refusal:
        session.call("POST", "/api/liquid/runs", {"liquidrun": {}})
    assert refusal.value.status == 201


def test_liquid_reply_must_be_finite_json(case, tmp_path):
    pytest.importorskip("backend.liquidcooling.run_manager")

    class Runs(LiquidRuns):
        def get(self, run_id):
            return {"value": float("nan")}

    with pytest.raises(ds.ServerError) as refusal:
        ds.DirectSession(case, tmp_path, liquid_manager=Runs()).call("GET", "/api/liquid/runs/r1")
    assert refusal.value.status == 400


# ── 5. the air run through the session, and the front door ──────────────────

def _compile(operation, project, project_path, *, timestamp=None, options=None, **_):
    return {"cfdrunFilename": "X.cfdrun", "cfdrunJson": json.dumps(_manifest()), "stlFiles": dict(STL),
            "manifestErrors": [], "modelCount": 3, "preflight": {"seedCollisions": []}, "preflightStops": [],
            "hybrid": {"hasHybridFanServer": False, "budgetVersion": None},
            "simulationMode": {"mode": "steady", "airMode": "steady", "liquidMode": "steady"},
            "implementation": {"node": "test"}}


@pytest.fixture
def project(case):
    path = case / "X.cfdproj"
    path.write_text(json.dumps({"schemaVersion": 5, "runDefaults": {"meshConfig": {}, "simulationConfig": {}}}))
    return path


def test_air_run_goes_through_the_in_process_session(project, case, stand_in, monkeypatch):
    from backend.api import air, air_metrics
    monkeypatch.setattr(air, "SESSION_MODULE", "backend.api.direct_session")
    monkeypatch.setattr(air, "compile_project", _compile)
    monkeypatch.setattr(air_metrics, "compute_air_metrics", lambda run, manifest, project: {"read": True})
    assert air._session_api() == (ds.DirectSession, ds.ServerError)

    result = air.run_air_case(project, mesh_timeout=60, solve_timeout=60)

    assert result["status"] == "succeeded", result["failure"]
    stages = result["stages"]
    assert stages["server"]["port"] == 0 and stages["server"]["stop"] == {"jobsAborted": [], "liquidRunsCancelled": []}
    assert (stages["mesh"]["returncode"], stages["mesh"]["checkMeshOk"], stages["mesh"]["cells"]) == (0, True, 4321)
    assert stages["solve"]["solveAssessment"]["verdict"] == "CONVERGED"
    assert stages["solve"]["runDir"] == str(case / "log" / "2026-01-01-10-00-00_X")
    assert stages["mesh"]["cfdrunSha256"] == stages["solve"]["cfdrunSha256"] == _sha(
        (case / "model" / "X.cfdrun").read_bytes())
    assert result["metrics"]["convergence"]["verdict"] == "CONVERGED" and result["metrics"]["fields"] == {"read": True}
    run_dir = Path(result["runDir"])
    assert Path(result["artifacts"]["serverLog"]) == run_dir / "server.log"
    assert "stand-in manifest" in (run_dir / "mesh-job.log").read_text()
    assert not list(run_dir.glob(".server-*"))


def test_air_run_reports_a_refused_job_as_a_stage_failure(project, stand_in, monkeypatch):
    from backend.api import air
    monkeypatch.setattr(air, "SESSION_MODULE", "backend.api.direct_session")
    monkeypatch.setattr(air, "compile_project", lambda *a, **k: {**_compile(*a, **k), "stlFiles": {"a.txt": "x"}})
    result = air.run_air_case(project, stages=("mesh",))
    assert result["status"] == "failed" and result["failure"]["stage"] == "mesh"
    assert "Invalid STL filename" in result["failure"]["reason"]


@pytest.fixture
def front_door(monkeypatch):
    import backend.api
    from backend.aircooling.runner import gpu_newton
    dceval = importlib.import_module("dceval")
    seen = []

    def run_air_case(cfdproj, **kwargs):
        seen.append({"cfdproj": cfdproj, "cards": os.environ.get(ds.GPU_ENV), **kwargs})
        return {"status": "succeeded", "failure": None, "artifacts": {"resultJson": "/runs/r/result.json"},
                "inputs": {"solverEngine": kwargs["solver_engine"], "stages": list(kwargs["stages"])},
                "stages": {"mesh": {"cells": 10, "checkMeshOk": True}}, "metrics": {"convergence": {"verdict": "CONVERGED"}}}

    monkeypatch.setattr(backend.api, "run_air_case", run_air_case)
    monkeypatch.setattr(gpu_newton, "configuration", lambda env=None: {})
    monkeypatch.setenv(ds.GPU_ENV, "GPU-before")
    return dceval, seen


def test_evaluate_passes_the_solver_cards_and_processes(front_door):
    dceval, seen = front_door
    result = dceval.evaluate("X.cfdproj", "runs/x", solver="gpuNewton", gpus=["GPU-aaaa", "GPU-bbbb"], processes=4,
                             end_time=200, solve_timeout=600)
    call = seen[0]
    assert (call["cfdproj"], call["case_dir"], call["solver_engine"]) == ("X.cfdproj", "runs/x", "gpuNewton")
    assert call["cards"] == "GPU-aaaa,GPU-bbbb" and os.environ[ds.GPU_ENV] == "GPU-before"
    assert call["run_settings"] == {"meshConfig": {"parallelMesh": True, "meshProcess": 4},
                                    "simulationConfig": {"parallelSolve": True, "solveProcess": 4, "endTime": 200}}
    assert (call["max_processes"], call["stages"], call["solve_timeout"]) == (4, ("mesh", "solve"), 600)
    assert result["resultPath"] == "/runs/r/result.json" and result["status"] == "succeeded"
    assert dceval.summary(result)["convergence"] == "CONVERGED" and dceval.summary(result)["cells"] == 10
    dceval.evaluate("X.cfdproj", solver="gpuNewton")            # no cards named: the environment's stay
    assert seen[1]["cards"] == "GPU-before" and seen[1]["case_dir"] is None
    dceval.evaluate("X.cfdproj", stages=("solve",))             # the GPU solver is the default
    assert seen[2]["solver_engine"] == "gpuNewton" and seen[2]["stages"] == ("solve",)
    dceval.evaluate("X.cfdproj", solver="openfoam")
    assert seen[3]["solver_engine"] == "openfoam"


@pytest.mark.parametrize("kwargs, message", [
    (dict(solver="other"), "solver engine must be one of"), (dict(solver="openfoam", gpus=[0]), "gpus names the cards"),
    (dict(processes=0), "processes must be"), (dict(solver="gpuNewton", gpus=[]), "names no card"),
])
def test_evaluate_refuses_invalid_arguments(front_door, kwargs, message):
    dceval, seen = front_door
    with pytest.raises(ValueError, match=message):
        dceval.evaluate("X.cfdproj", **kwargs)
    assert not seen


def test_command_line_prints_one_json_line(front_door, capsys):
    dceval, seen = front_door
    cli = importlib.import_module("dceval.__main__")
    code = cli.main(["evaluate", "X.cfdproj", "--case-dir", "runs/x", "--solver", "gpuNewton", "--gpus",
                     "GPU-aaaa", "--processes", "2", "--stages", "solve"])
    out = capsys.readouterr().out
    assert code == 0 and out.count("\n") == 1
    printed = json.loads(out)
    assert printed["status"] == "succeeded" and printed["resultPath"] == "/runs/r/result.json"
    assert (seen[0]["cards"], seen[0]["max_processes"], seen[0]["stages"]) == ("GPU-aaaa", 2, ("solve",))
    assert cli.main(["evaluate", "X.cfdproj", "--full"]) == 0
    assert json.loads(capsys.readouterr().out)["artifacts"] == {"resultJson": "/runs/r/result.json"}
    assert cli.main(["evaluate", "X.cfdproj", "--solver", "openfoam", "--gpus", "0"]) == 2  # cards, no GPU solver
    assert json.loads(capsys.readouterr().out)["failure"]["stage"] == "input"
