"""Process completion must carry the original job-owned physical assessment."""
import hashlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path
import pytest

SERVER_PATH = Path(__file__).resolve().parents[2] / "server/server.py"
FIXTURE = Path(__file__).parent / "fixtures/air_solve_assessment/actual-not-converged.json"
INPUT = '{"schemaVersion": 2, "revision": "immutable-status-contract"}'
SHA = hashlib.sha256(INPUT.encode()).hexdigest()

class Handler:
    def __init__(self, payload=None):
        raw = json.dumps(payload or {}).encode()
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
    def send_response(self, value): self.status = value
    def send_header(self, *_): pass
    def end_headers(self): pass
    @property
    def response(self): return json.loads(self.wfile.getvalue())

@pytest.fixture
def server(monkeypatch):
    monkeypatch.syspath_prepend(str(SERVER_PATH.parent))
    spec = importlib.util.spec_from_file_location("solve_verdict_server_under_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DCTWIN_AVAILABLE", True)
    return module

def owned_report(case, *, verdict=None):
    run = case / "log/run-this-job"
    run.mkdir(parents=True)
    log = run / "solver.log"
    log.write_text("actual fixture report, no numerical solver launched\n")
    report = json.loads(FIXTURE.read_bytes())
    report["log"] = str(log)
    if verdict == "CONVERGED":
        report.update(verdict=verdict)
        report["residuals"]["passed"] = True
        report["sensors"].update(passed=True, status="PASSED")
    elif verdict == "INCONCLUSIVE":
        report.update(verdict=verdict)
        report["residuals"]["passed"] = True
    elif verdict and verdict.startswith("TRANSIENT_"):
        # The actual transient assessor, not a hand-written steady adaptation.
        from backend.aircooling.runner.convergence import assess_run
        (run / "system").mkdir()
        (run / "system/controlDict").write_text("application buoyantBoussinesqPimpleFoam_new;\nendTime 1;\ndeltaT .1;\n")
        log.write_text("Exec : buoyantBoussinesqPimpleFoam_new\nTime = 1\nCourant Number mean: .1 max: .3\nEnd\n" if verdict == "TRANSIENT_COMPLETED" else "Exec : buoyantBoussinesqPimpleFoam_new\nTime = 0.5\nEnd\n")
        report = assess_run(run, log_path=log, write=False)
        assert report["verdict"] == verdict
    elif verdict: report["verdict"] = verdict
    (run / "convergence.json").write_text(json.dumps(report))
    execution = {"format": "air-run-execution-receipt/v1", "status": "accepted", "solverCommand": "fixture", "result": {"cfdrunSha256": SHA, "runRelativePath": run.relative_to(case).as_posix()}}
    (run / "air-run-execution-receipt.json").write_text(json.dumps(execution))
    return run, report

@pytest.mark.parametrize("verdict,mode", [("NOT_CONVERGED","steady"),("CONVERGED","steady"),("INCONCLUSIVE","steady"),("TRANSIENT_COMPLETED","transient"),("TRANSIENT_INCOMPLETE","transient")])
def test_original_steady_and_transient_reports(server, tmp_path, verdict, mode):
    run, original = owned_report(tmp_path, verdict=verdict)
    before = (run / "convergence.json").read_bytes()
    result = server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)
    assert result["verdict"] == original["verdict"] == verdict
    assert result["mode"] == mode
    assert result["reportSha256"] == hashlib.sha256(before).hexdigest()
    assert (run / "convergence.json").read_bytes() == before
    if verdict == "NOT_CONVERGED":
        assert original["residuals"]["iterations"] == 2000
        assert original["sensors"]["status"] == "NO_SENSORS"
        assert any("p_rgh" in reason for reason in result["reasons"])

@pytest.mark.parametrize("mutation", ["missing", "bad-json", "mismatched-input", "mismatched-run", "unknown", "contradictory", "foreign-case", "report-symlink", "log-symlink"])
def test_no_unrelated_or_invalid_report_can_claim_success(server, tmp_path, mutation):
    case = tmp_path / "own"
    run, report = owned_report(case, verdict="CONVERGED")
    reportpath = run / "convergence.json"
    receiptpath = run / "air-run-execution-receipt.json"
    if mutation == "missing": reportpath.unlink()
    elif mutation == "bad-json": reportpath.write_text("{")
    elif mutation.startswith("mismatched"):
        data=json.loads(receiptpath.read_bytes());data["result"]["cfdrunSha256" if mutation == "mismatched-input" else "runRelativePath"]="different";receiptpath.write_text(json.dumps(data))
    elif mutation in ["unknown", "contradictory"]:
        report["verdict"]="MAGIC" if mutation=="unknown" else "NOT_CONVERGED";reportpath.write_text(json.dumps(report))
    elif mutation == "foreign-case": run, _=owned_report(tmp_path/"other",verdict="CONVERGED")
    else:
        path=reportpath if mutation=="report-symlink" else run/"solver.log"
        elsewhere=tmp_path/"other-file";elsewhere.write_bytes(path.read_bytes());path.unlink();path.symlink_to(elsewhere)
    result=server._solve_job_assessment(case_dir=case,run_dir=run,cfdrun_sha256=SHA)
    assert result["verdict"] == "UNKNOWN"
    assert result["reportSha256"] is None
    assert result["reasons"]

def test_real_submit_worker_sse_and_poll_carry_actual_nonconvergence(server, tmp_path, monkeypatch):
    run, report=owned_report(tmp_path)
    other, _=owned_report(tmp_path/"other",verdict="CONVERGED")
    monkeypatch.setattr(server,"CASE_DIR",tmp_path)
    workers=[]
    monkeypatch.setattr(server,"_start_job_worker",workers.append)
    monkeypatch.setattr(server,"_release_idle_software_browser",lambda:{"status":"not-started"})
    proc=types.SimpleNamespace(stdout=io.StringIO(f"Created run directory: {run}\n"),returncode=0,wait=lambda:0)
    monkeypatch.setattr(server,"_spawn_job_process",lambda *a,**k:proc)
    monkeypatch.setattr(server,"_start_snapshot_input",lambda *a:None)
    monkeypatch.setattr(server,"_join_job_cleanup",lambda *a:None)
    monkeypatch.setattr(server,"_summarize_solve_mesh_quality_rejection",lambda *a:{})
    handler=Handler({"caseDir":str(tmp_path),"cfdrunJson":INPUT,"cfdrunFilename":"status.cfdrun","stlFiles":{},"solveOnly":True})
    server.handle_run_mesh(handler)
    assert handler.status == 200,handler.response
    jid=handler.response["jobId"]
    # The runner receives the real published snapshot (v2 input paths may be canonicalized).
    accepted_sha=hashlib.sha256((tmp_path/"model/status.cfdrun").read_bytes()).hexdigest()
    execution_path=run/"air-run-execution-receipt.json"
    execution=json.loads(execution_path.read_bytes());execution["result"]["cfdrunSha256"]=accepted_sha
    execution_path.write_text(json.dumps(execution))
    server._LATEST_SOLVE_RUN_DIR=other
    workers[0]()
    # A different tab's legacy monitor selection cannot grant this job success.
    server._LATEST_SOLVE_RUN_DIR=other
    poll=Handler();server.handle_job_status(poll,{"job_id":[jid]})
    assert poll.response["done"] is True and poll.response["returncode"] == 0
    assert poll.response["solveAssessment"]["verdict"] == "NOT_CONVERGED"
    assert poll.response["solveAssessment"]["cfdrunSha256"] == accepted_sha == handler.response["cfdrunSha256"]
    sse=Handler();server.handle_log_stream(sse,{"job_id":[jid]})
    final=json.loads(sse.wfile.getvalue().decode().strip().split("data: ")[-1])
    assert final["solveAssessment"] == poll.response["solveAssessment"]
    assert final["cancelled"] is False
    assert (run/"convergence.json").read_text()==json.dumps(report)

def test_cancelled_finish_discards_positive_report(server):
    server.JOBS["cancel"]=server._new_job_state([],cancelRequested=True,solveAssessment={"verdict":"CONVERGED"})
    server._finish_job("cancel",0,quality={"solveAssessment":{"verdict":"CONVERGED"}})
    job=server.JOBS["cancel"]
    assert job["done"] and job["cancelled"] and job["returncode"] == -15
    assert "solveAssessment" not in job


def test_report_replaced_after_read_cannot_claim_success(server, tmp_path, monkeypatch):
    run, report = owned_report(tmp_path, verdict="CONVERGED")
    path = run / "convergence.json"
    read_bytes = Path.read_bytes
    def replace_after_read(current):
        raw = read_bytes(current)
        if current == path:
            # Preserve returned old bytes, but replace this owned report before
            # validation finishes. A mixed report read has no positive verdict.
            replacement = run / "replacement.json"
            replacement.write_bytes(raw)
            replacement.replace(path)
        return raw
    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    result = server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)
    assert result["verdict"] == "UNKNOWN"
    assert result["reportSha256"] is None
    assert any("changed while reading" in reason for reason in result["reasons"])


def test_return_air_rule_report_converges_with_failing_residuals(server, tmp_path):
    # a report under the owner's 2026-10-20 rule: return air flat, residual
    # leg failed -- CONVERGED, and the server must not call it inconsistent
    # (Slab, 2026-10-20: stopped flat at iteration 1041, shown UNKNOWN)
    run, report = owned_report(tmp_path)
    report.update(verdict="CONVERGED",
                  rule="every return-air temperature changed then plateaued (within 0.5 K over the last window); residuals are diagnostics")
    report["residuals"]["passed"] = False
    report["sensors"].update(passed=True, status="OK")
    (run / "convergence.json").write_text(json.dumps(report))
    result = server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)
    assert result["verdict"] == "CONVERGED" and result["mode"] == "steady"
    report["verdict"] = "NOT_CONVERGED"
    (run / "convergence.json").write_text(json.dumps(report))
    assert server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)["verdict"] == "UNKNOWN"


def test_a_too_short_run_reports_inconclusive_with_its_reason_first(server, tmp_path):
    # 2026-10-22: a steady run shorter than the return-air window's minimum is
    # INCONCLUSIVE; the panel shows the decisive (return-air) reason first and
    # the residual diagnostics after it
    from backend.aircooling.runner.convergence import assess_run
    run = tmp_path / "log/short-run"
    (run / "system").mkdir(parents=True)
    (run / "system/controlDict").write_text("application buoyantBoussinesqSimpleFoam_new;\nendTime 20;\n")
    log = run / "solver.log"
    log.write_text("Exec : buoyantBoussinesqSimpleFoam_new\n" + "".join(
        f"Time = {i}\nsmoothSolver:  Solving for Ux, Initial residual = 0.5, Final residual = 0.01, No Iterations 2\n"
        for i in range(1, 21)) + "End\n")
    probes = run / "postProcessing/probes/0"
    probes.mkdir(parents=True)
    probes.joinpath("T").write_text("# Probe 0 (0 0 0)\n#  Time\n" + "".join(
        f"{i} {295.15 - 0.015 * i:.4f}\n" for i in range(1, 21)))
    report = assess_run(run, log_path=log)
    assert report["verdict"] == "INCONCLUSIVE" and report["verdictReason"].startswith("too-short: 20 iterations")
    execution = {"format": "air-run-execution-receipt/v1", "status": "accepted", "solverCommand": "fixture",
                 "result": {"cfdrunSha256": SHA, "runRelativePath": run.relative_to(tmp_path).as_posix()}}
    (run / "air-run-execution-receipt.json").write_text(json.dumps(execution))
    result = server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)
    assert result["verdict"] == "INCONCLUSIVE" and result["mode"] == "steady"
    assert result["reasons"][0].startswith("too-short: 20 iterations < minimum 100")
    assert any(reason.startswith("Ux:") for reason in result["reasons"][1:])


def test_an_open_energy_balance_is_not_converged_and_says_why(server, tmp_path):
    # owner rule 2026-10-30: return air flat but the ACUs removing a fraction
    # of the declared heat (Alibaba-sige at iteration 204) is NOT_CONVERGED,
    # and the panel shows the energy reason first
    run, report = owned_report(tmp_path)
    reason = "energy balance open: the ACUs remove 21.5 kW of 994.9 kW declared (-97.8 %)"
    report.update(verdict="NOT_CONVERGED",
                  rule="every return-air temperature changed then plateaued (within 0.5 K over the last window), "
                       "and the heat the ACUs remove closed on the declared heat; residuals are diagnostics",
                  energy={"passed": False, "status": "OPEN", "reasons": [reason]})
    report["sensors"].update(passed=True, status="OK", reasons=[])
    (run / "convergence.json").write_text(json.dumps(report))
    result = server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)
    assert result["verdict"] == "NOT_CONVERGED" and result["reasons"][0] == reason
    report["verdict"] = "CONVERGED"
    (run / "convergence.json").write_text(json.dumps(report))
    assert server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)["verdict"] == "UNKNOWN"
    report["energy"] = {"passed": True, "status": "CLOSED", "reasons": []}
    (run / "convergence.json").write_text(json.dumps(report))
    assert server._solve_job_assessment(case_dir=tmp_path, run_dir=run, cfdrun_sha256=SHA)["verdict"] == "CONVERGED"
