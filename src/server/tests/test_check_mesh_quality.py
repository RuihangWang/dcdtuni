"""Check-only API must bind full topology/geometry verdict, never infer from exit zero."""
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

@pytest.fixture
def route(monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[1]/"server.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location("quality_check_server",path)
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    spec.loader.exec_module(module)
    module.CASE_DIR=tmp_path
    replies=[]
    monkeypatch.setattr(module,"_send_json",lambda handler,code,data:replies.append((code,data)))
    monkeypatch.setattr(module,"_read_body",lambda handler:{"cfdrunFilename":"test.cfdrun"})
    monkeypatch.setattr(module.threading,"Thread",lambda target,**kw:SimpleNamespace(start=target))
    monkeypatch.setattr(module.threading,"Timer",lambda *_a,**_kw:SimpleNamespace(start=lambda:None,cancel=lambda:None))
    module.replies=replies
    (tmp_path/"model").mkdir()
    (tmp_path/"model/test.cfdrun").write_text(json.dumps({"meshConfig":{"meshProcess":99}}))
    return module

def mesh(case,ranks=2,cells=1):
    (case/"Mesh/system").mkdir(parents=True)
    (case/"Mesh/system/decomposeParDict").write_text(f"numberOfSubdomains {ranks};")
    for rank in range(ranks):
        poly=case/f"Mesh/processor{rank}/constant/polyMesh"
        poly.mkdir(parents=True)
        for name in ["points","faces","owner","neighbour","boundary"]:
            (poly/name).write_text(f'FoamFile {{ note "nCells:{cells}"; }}\n0\n(\n)\n')

def invoke(route,monkeypatch,output="Mesh OK.\n",mutate=None):
    commands=[]
    def popen(command,**kwargs):
        commands.append(command)
        if mutate:mutate()
        return SimpleNamespace(stdout=io.StringIO(output),returncode=0,pid=99999,wait=lambda:None)
    monkeypatch.setattr(route.subprocess,"Popen",popen)
    route.handle_run_check_mesh(None)
    return commands,route.replies[-1]

def test_full_flags_use_actual_partition_count_and_bind_receipt(route,tmp_path,monkeypatch):
    mesh(tmp_path)
    commands,(code,body)=invoke(route,monkeypatch)
    assert code==200
    from backend.aircooling.runner.air_mesh_quality_receipt import resolve_checkmesh_binary
    assert commands==[["mpirun","-np","2",resolve_checkmesh_binary(),"-case",str(tmp_path/"Mesh"),"-constant","-allTopology","-allGeometry","-parallel"]]
    job=route.JOBS[body["jobId"]]
    assert job["done"] and job["returncode"]==0 and job["checkMeshOk"]
    saved=json.loads((tmp_path/"mesh-quality-receipt.json").read_text())
    assert saved["status"]=="accepted" and saved["ranks"]==2
    from backend.aircooling.runner.air_mesh_quality_receipt import verify_air_mesh_quality_receipt
    verify_air_mesh_quality_receipt(tmp_path/"mesh-quality-receipt.json",tmp_path/"Mesh",tmp_path/"model/test.cfdrun",require_accepted=True)

def test_exit_zero_failed_mesh_verdict_is_job_error(route,tmp_path,monkeypatch):
    mesh(tmp_path)
    _,(code,body)=invoke(route,monkeypatch,"Failed 3 mesh checks.\n")
    assert code==200
    job=route.JOBS[body["jobId"]]
    assert job["done"] and job["returncode"]!=0 and not job["checkMeshOk"]
    saved=json.loads((tmp_path/"mesh-quality-receipt.json").read_text())
    assert saved["status"]=="rejected" and saved["failedChecks"]==3

def test_empty_mesh_rejects_without_starting_checker(route,tmp_path,monkeypatch):
    mesh(tmp_path,cells=0)
    commands,(code,body)=invoke(route,monkeypatch)
    assert code==400 and "empty" in body["error"]
    assert not commands

def test_input_mutation_during_checker_cannot_authorize_mesh(route,tmp_path,monkeypatch):
    mesh(tmp_path)
    _,(code,body)=invoke(route,monkeypatch,mutate=lambda:(tmp_path/"model/test.cfdrun").write_text("{}"))
    assert code==200
    job=route.JOBS[body["jobId"]]
    assert job["returncode"]!=0 and not job["checkMeshOk"]
    assert not (tmp_path/"mesh-quality-receipt.json").exists()

def test_incomplete_partition_layout_rejects(route,tmp_path,monkeypatch):
    mesh(tmp_path)
    (tmp_path/"Mesh/processor1/constant/polyMesh/owner").unlink()
    commands,(code,_)=invoke(route,monkeypatch)
    assert code==404 and not commands


def test_deadline_stops_mpi_group_and_cannot_write_accepted_receipt(route,tmp_path,monkeypatch):
    import signal
    mesh(tmp_path)
    killed=[]
    monkeypatch.setattr(route.os,"killpg",lambda pid,sig:killed.append((pid,sig)))
    monkeypatch.setattr(route.threading,"Timer",
        lambda duration,callback:SimpleNamespace(start=callback,cancel=lambda:None))
    _,(code,body)=invoke(route,monkeypatch)
    assert code==200
    assert killed==[(99999,signal.SIGKILL)]
    job=route.JOBS[body["jobId"]]
    assert job["returncode"]==124 and not job["checkMeshOk"]
    assert not (tmp_path/"mesh-quality-receipt.json").exists()

def test_case_lock_prevents_checker_overlapping_solver(route,tmp_path,monkeypatch):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock
    mesh(tmp_path)
    with case_execution_lock(tmp_path):
        commands,(code,body)=invoke(route,monkeypatch)
    assert code==409 and body["code"]=="case_busy" and not commands

def test_mesh_mutation_during_checker_cannot_authorize_mesh(route,tmp_path,monkeypatch):
    mesh(tmp_path)
    _,(code,body)=invoke(route,monkeypatch,mutate=lambda:(tmp_path/"Mesh/processor0/constant/polyMesh/points").write_text("changed"))
    assert code==200
    job=route.JOBS[body["jobId"]]
    assert job["returncode"]!=0 and not job["checkMeshOk"]
    assert not (tmp_path/"mesh-quality-receipt.json").exists()


def test_postcheck_identity_work_cannot_exceed_whole_job_budget(route,tmp_path,monkeypatch):
    mesh(tmp_path)
    clock=iter([0,0,301])
    monkeypatch.setattr(route.time,"monotonic",lambda:next(clock))
    _,(code,body)=invoke(route,monkeypatch)
    assert code==200
    job=route.JOBS[body["jobId"]]
    assert job["returncode"]==124 and not job["checkMeshOk"]
    assert "meshQualityReceipt" not in job
    assert not (tmp_path/"mesh-quality-receipt.json").exists()


def test_solve_preflight_quality_refusal_retains_native_findings(route):
    result = route._summarize_solve_mesh_quality_rejection([
        "***Error in face tets: 77 faces with low quality or negative volume decomposition tets.",
        "Failed 4 mesh checks.",
        "RuntimeError: air solve requires accepted strict mesh quality: mesh quality receipt is rejected, not accepted",
    ], 1)
    assert result["checkMeshOk"] is False and result["failedChecks"] == 4
    assert result["meshWarnings"][0]["code"] == "bad_face_tets"
    assert result["meshWarnings"][0]["count"] == 77


@pytest.mark.parametrize("lines,code", [
    (["RuntimeError: solver did not converge"], 1),
    (["FOAM FATAL ERROR: invalid boundary type"], 1),
    (["Failed 4 mesh checks."], 1),
    (["RuntimeError: air solve requires accepted strict mesh quality: old message"], 0),
])
def test_other_solve_errors_or_success_do_not_invent_mesh_quality(route,lines,code):
    assert route._summarize_solve_mesh_quality_rejection(lines,code) is None
