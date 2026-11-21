"""Cancellation must stop the actual worker before exposing a terminal job.

These exercise real API handler/worker closures with controlled process/pipe
boundaries. No browser, server, CFD executable or external workspace is started.
"""
import importlib.util
import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REAL_THREAD=threading.Thread

class Pipe:
    def __init__(self):
        self.entered=threading.Event();self.release=threading.Event()
    def readline(self):
        self.entered.set()
        assert self.release.wait(2), 'test must release simulated descendant stdout'
        return ''

class Process:
    pid=987654
    def __init__(self,blocked=False):
        self.returncode=None
        self.stdin=io.TextIOWrapper(io.BytesIO(),encoding='utf-8')
        self.stdout=Pipe() if blocked else io.StringIO('Mesh OK.\n')
    def poll(self):return self.returncode
    def wait(self,timeout=None):
        if self.returncode is None:self.returncode=0
        return self.returncode

@pytest.fixture
def route(monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[1]/'server.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location('cancel_test_server',path)
    m=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,m);spec.loader.exec_module(m)
    m.CASE_DIR=tmp_path;m.DCTWIN_AVAILABLE=True;m.JOBS={};m.request={'meshOnly':True}
    m.replies=[];m.workers=[];m.spawned=[];m.terminated=[]
    monkeypatch.setattr(m,'_read_body',lambda _,**kwargs:
        (dict(m.request),json.dumps(m.request).encode()) if kwargs.get('include_bytes') else dict(m.request))
    monkeypatch.setattr(m,'_send_json',lambda _,code,data:m.replies.append((code,data)))
    monkeypatch.setattr(m,'_publish_case_inputs',lambda *_a,**_k:(tmp_path/'model/test.cfdrun','sha'))
    from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
    m.input_snapshot=CfdrunSnapshot(b'{\"schemaVersion\":2,\"extension\":\"owned input\"}',tmp_path,tmp_path/'model/test.cfdrun',stl_batch_sha256=surface_batch_sha256(tmp_path))
    monkeypatch.setattr(m,'_publish_case_input_snapshot',lambda *_a,**_k:m.input_snapshot)
    monkeypatch.setattr(m,'_release_idle_software_browser',lambda:{'status':'closed'})
    monkeypatch.setattr(m,'_start_job_worker',lambda target:m.workers.append(target),raising=False)
    def popen(*args,**kwargs):
        proc=Process();m.spawned.append(proc);return proc
    monkeypatch.setattr(m.subprocess,'Popen',popen)
    def terminate(proc):
        m.terminated.append(proc);proc.returncode=-15;return True
    m.real_terminate_job_tree = m._terminate_job_tree
    monkeypatch.setattr(m,'_terminate_job_tree',terminate)
    return m

def start(route):
    route.request={'meshOnly':True};route.handle_run_mesh(None)
    code,body=route.replies[-1];assert code==200
    return body['jobId']

def abort(route,job):
    route.request={'jobId':job};route.handle_abort_job(None)
    return route.replies[-1]

def test_cancel_queued_job_never_launches_after_ack(route):
    job=start(route);code,body=abort(route,job)
    assert code==200 and body['status']=='aborting' and body['done'] is False
    assert route.JOBS[job]['cancelRequested'] and not route.JOBS[job]['done']
    route.workers[0]()
    assert not route.spawned
    result=route.JOBS[job]
    assert result['done'] and result['returncode']==-15 and result['cancelled']
    assert result['checkMeshOk'] is False


def test_cancel_during_browser_release_never_late_spawns(route,monkeypatch):
    entered=threading.Event();release=threading.Event()
    def browser_release():
        entered.set();assert release.wait(2);return {'status':'closed'}
    monkeypatch.setattr(route,'_release_idle_software_browser',browser_release)
    job=start(route);worker=REAL_THREAD(target=route.workers[0],daemon=True);worker.start()
    try:
        assert entered.wait(1)
        _,body=abort(route,job)
        assert body['status']=='aborting' and not route.JOBS[job]['done']
    finally:
        release.set();worker.join(2)
    assert not worker.is_alive() and not route.spawned
    assert route.JOBS[job]['done'] and route.JOBS[job]['returncode']==-15


def test_abort_ack_waits_for_stdout_drain_and_real_worker_finally(route,monkeypatch):
    proc=Process(blocked=True)
    monkeypatch.setattr(route.subprocess,'Popen',lambda *_a,**_k:proc)
    job=start(route);worker=REAL_THREAD(target=route.workers[0],daemon=True);worker.start()
    try:
        assert proc.stdout.entered.wait(1)
        _,body=abort(route,job)
        assert proc.poll()==-15
        assert body['status']=='aborting' and body['done'] is False
        assert not route.JOBS[job]['done']
    finally:
        proc.stdout.release.set();worker.join(2)
    assert not worker.is_alive()
    result=route.JOBS[job]
    assert result['done'] and result['cancelled'] and result['returncode']==-15
    assert result['checkMeshOk'] is False


def test_repeated_abort_is_idempotent_and_cannot_cancel_other_job(route):
    job=start(route);other=start(route)
    first=abort(route,job);second=abort(route,job)
    assert first==second and first[1]['status']=='aborting'
    assert not route.JOBS[other].get('cancelRequested',False)
    route.workers[0]()
    _,body=abort(route,job)
    assert body['status']=='aborted' and body['done'] is True
    assert not route.spawned and not route.JOBS[other]['done']
    assert sum('aborted' in line.lower() for line in route.JOBS[job]['lines'])<=1


def test_naturally_finished_job_remains_already_done(route):
    job=start(route);route.workers[0]()
    before=dict(route.JOBS[job]);_,body=abort(route,job)
    assert body['status']=='already_done' and body['done'] is True
    assert route.JOBS[job]['returncode']==0 and not route.JOBS[job].get('cancelled',False)
    assert route.JOBS[job]==before


def test_unknown_abort_is_404_and_registry_unchanged(route):
    job=start(route);before=dict(route.JOBS)
    code,_=abort(route,'no-such-job')
    assert code==404 and route.JOBS==before


def test_status_and_final_sse_expose_cancellation_terminal_fields(route):
    job=start(route);abort(route,job);route.workers[0]()
    route.handle_job_status(None,{'job_id':[job]})
    status=route.replies[-1][1]
    assert status['done'] and status['returncode']==-15 and status['cancelled']
    assert status['cancelRequested'] and status['checkMeshOk'] is False
    handler=SimpleNamespace(send_response=lambda *_:None,send_header=lambda *_:None,end_headers=lambda:None,wfile=io.BytesIO())
    route.handle_log_stream(handler,{'job_id':[job]})
    data=[json.loads(line[6:]) for line in handler.wfile.getvalue().decode().splitlines() if line.startswith('data: ')]
    final=data[-1]
    assert final['done'] and final['returncode']==-15 and final['cancelled'] and final['cancelRequested']
    assert final['checkMeshOk'] is False


def test_spawn_publication_is_atomic_with_abort(route, monkeypatch):
    entered = threading.Event()
    publish = threading.Event()
    proc = Process(blocked=True)
    def launch(*_a, **_kw):
        entered.set()
        assert publish.wait(2)
        return proc
    monkeypatch.setattr(route.subprocess, "Popen", launch)
    job = start(route)
    worker = REAL_THREAD(target=route.workers[0], daemon=True)
    worker.start()
    cancel = REAL_THREAD(target=lambda: abort(route, job), daemon=True)
    try:
        assert entered.wait(1)
        cancel.start()
        publish.set()
        assert proc.stdout.entered.wait(1)
        cancel.join(1)
        assert not cancel.is_alive()
        assert route.replies[-1][1]["status"] == "aborting"
        assert proc.returncode == -15 and not route.JOBS[job]["done"]
    finally:
        publish.set()
        proc.stdout.release.set()
        worker.join(2)
        cancel.join(2)
    assert not worker.is_alive()
    assert route.JOBS[job]["cancelled"] and route.JOBS[job]["done"]


def test_cancel_during_quality_finalization_cannot_reauthorize_success(route, monkeypatch, tmp_path):
    receipt = tmp_path / "mesh-quality-receipt.json"
    def quality(_lines, _rc):
        receipt.write_text('{"status":"accepted"}')
        abort(route, job)
        return {"checkMeshOk": True, "failedChecks": 0, "meshQualityReceipt": {"status": "accepted"}}
    monkeypatch.setattr(route, "_summarize_mesh_run_quality", quality)
    job = start(route)
    route.workers[0]()
    result = route.JOBS[job]
    assert result["done"] and result["returncode"] == -15 and result["cancelled"]
    assert result["checkMeshOk"] is False and "meshQualityReceipt" not in result
    assert not receipt.exists()


def prepare_check_only(route, monkeypatch, tmp_path):
    import contextlib
    from backend.aircooling.runner import mesh_layout, openfoam_patch_area
    # Load the receipt's direct imports before patching the public mesh reader;
    # otherwise a first import would retain this test's fake identity globally.
    from backend.aircooling.runner import air_mesh_quality_receipt
    (tmp_path / "Mesh").mkdir(exist_ok=True)
    monkeypatch.setattr(mesh_layout, "mesh_directories", lambda _: [tmp_path / "Mesh/constant/polyMesh"])
    monkeypatch.setattr(openfoam_patch_area, "require_nonempty_mesh", lambda _: None)
    monkeypatch.setattr(openfoam_patch_area, "mesh_identity_sha256", lambda _: "mesh-sha")
    monkeypatch.setattr(route.threading, "Timer", lambda *_a, **_kw: SimpleNamespace(start=lambda: None, cancel=lambda: None))
    route.lock_held = False
    @contextlib.contextmanager
    def locked(_):
        assert not route.lock_held
        route.lock_held = True
        try:
            yield
        finally:
            route.lock_held = False
    monkeypatch.setattr(route, "case_execution_lock", locked)
    route.request = {}
    route.handle_run_check_mesh(None)
    assert route.replies[-1][0] == 200
    return route.replies[-1][1]["jobId"]


def test_cancel_queued_check_only_never_spawns(route, monkeypatch, tmp_path):
    job = prepare_check_only(route, monkeypatch, tmp_path)
    abort(route, job)
    route.workers[0]()
    assert not route.spawned and not route.lock_held
    assert route.JOBS[job]["cancelled"] and route.JOBS[job]["returncode"] == -15
    assert not route.JOBS[job]["checkMeshOk"]


def test_check_only_abort_remains_pending_until_case_lock_released(route, monkeypatch, tmp_path):
    job = prepare_check_only(route, monkeypatch, tmp_path)
    proc = Process(blocked=True)
    monkeypatch.setattr(route.subprocess, "Popen", lambda *_a, **_kw: proc)
    worker = REAL_THREAD(target=route.workers[0], daemon=True)
    worker.start()
    try:
        assert proc.stdout.entered.wait(1) and route.lock_held
        _, body = abort(route, job)
        assert body["status"] == "aborting" and route.lock_held
        assert not route.JOBS[job]["done"]
    finally:
        proc.stdout.release.set()
        worker.join(2)
    assert not worker.is_alive() and not route.lock_held
    assert route.JOBS[job]["done"] and route.JOBS[job]["cancelled"]


def test_tree_stop_failure_does_not_publish_terminal_and_retry_is_possible(route, monkeypatch):
    job = start(route)
    proc = Process()
    route.JOBS[job]["proc"] = proc
    calls = []
    monkeypatch.setattr(route, "_terminate_job_tree", lambda p: calls.append(p) or False)
    code, body = abort(route, job)
    assert code == 500 and body["done"] is False
    assert route.JOBS[job]["cancelRequested"] and not route.JOBS[job]["done"]
    assert not route.JOBS[job]["_stop_started"]
    abort(route, job)
    assert calls == [proc, proc]


def test_natural_finalizer_and_abort_lock_order_cannot_deadlock(route, monkeypatch):
    job = start(route)
    proc = Process()
    proc.returncode = 0
    state = route.JOBS[job]
    state["proc"] = proc
    state["_stop_lock"].acquire()
    cancel = REAL_THREAD(target=lambda: abort(route, job), daemon=True)
    try:
        cancel.start()
        cancel.join(1)
        assert not cancel.is_alive()
        assert route.replies[-1][1]["status"] == "aborting"
        assert state["cancelRequested"] and not state["done"]
    finally:
        state["_stop_lock"].release()
    route._finish_job(job, 0)
    assert state["done"] and state["cancelled"] and state["returncode"] == -15


def test_tree_termination_refreshes_descendant_sessions_before_sigkill(route, monkeypatch):
    import signal
    groups = iter([{777}, {777}, {777, 778}])
    clock = iter(range(20))
    killed = []
    proc = Process()
    proc.pid = 777
    def signal_root(sig):
        if sig == signal.SIGKILL:
            proc.returncode = -9
    proc.send_signal = signal_root
    monkeypatch.setattr(route, "_descendant_pgids", lambda _: next(groups))
    monkeypatch.setattr(route, "_any_alive", lambda _: proc.returncode is None)
    monkeypatch.setattr(route.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(route.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(route.time, "sleep", lambda _: None)
    # Exercise the actual helper retained before the route fixture's mock.
    assert route.real_terminate_job_tree(proc, grace=1) is True
    assert (778, signal.SIGKILL) in killed
    assert proc._dcdtuni_job_pgids == {777, 778}


def test_zombie_group_cannot_hold_cancellation_open_forever(route, monkeypatch, tmp_path):
    fakeproc = tmp_path / "proc"
    (fakeproc / "123").mkdir(parents=True)
    (fakeproc / "123/stat").write_text("123 (mpi rank) Z 1 777 777 0")
    monkeypatch.setattr(route.os, "killpg", lambda *_: None)
    monkeypatch.setattr(route, "Path", lambda p: fakeproc if p == "/proc" else Path(p))
    assert route._any_alive({777}) is False
    (fakeproc / "123/stat").write_text("123 (mpi rank) S 1 777 777 0")
    assert route._any_alive({777}) is True


def test_receipt_cleanup_error_cannot_prevent_real_cancel_terminal(route, monkeypatch):
    job = start(route)
    abort(route, job)
    class FailedReceipt:
        def unlink(self, **_):
            raise PermissionError("fixture receipt is read-only")
    route._finish_job(job, 0, cancelled_receipt=FailedReceipt())
    assert route.JOBS[job]["done"] and route.JOBS[job]["returncode"] == -15
    assert route.JOBS[job]["cancelled"] and route.JOBS[job]["checkMeshOk"] is False
    assert any("receipt cleanup failed" in line.lower() for line in route.JOBS[job]["lines"])


class OwnedInputBuffer(io.BytesIO):
    def close(self):
        self.published = self.getvalue()
        super().close()


def test_job_uses_owned_snapshot_stdin_and_pins_sha(route, monkeypatch):
    proc=Process(); buffer=OwnedInputBuffer()
    proc.stdin=SimpleNamespace(buffer=buffer)
    launches=[]
    def launch(command, **kwargs):
        launches.append((command, kwargs));return proc
    monkeypatch.setattr(route.subprocess,'Popen',launch)
    job=start(route);route.workers[0]()
    command,kwargs=launches[0]
    assert '--cfdrun-stdin' in command and kwargs['stdin'] is route.subprocess.PIPE
    assert command[command.index('--from-cfdrun')+1]==str(route.input_snapshot.logical_path)
    assert command[command.index('--expected-cfdrun-sha256')+1]==route.input_snapshot.sha256
    assert command[command.index('--expected-stl-batch-sha256')+1]==route.input_snapshot.stl_batch_sha256
    assert buffer.published==route.input_snapshot.read_bytes() and buffer.closed
    assert route.JOBS[job]['done'] and route.JOBS[job]['returncode']==0
    assert all(value is not route.input_snapshot for value in route.JOBS[job].values())


def test_input_pipe_failure_cannot_publish_child_success(route, monkeypatch):
    class BrokenInput:
        closed=False
        def write(self,_):raise BrokenPipeError('controlled child refused input')
        def close(self):self.closed=True
    proc=Process();pipe=BrokenInput();proc.stdin=SimpleNamespace(buffer=pipe)
    monkeypatch.setattr(route.subprocess,'Popen',lambda *_a,**_k:proc)
    job=start(route);route.workers[0]()
    state=route.JOBS[job]
    assert pipe.closed and state['done'] and state['returncode']!=0
    assert not state['checkMeshOk']
    assert any('controlled child refused input' in line for line in state['lines'])


def test_terminal_waits_for_input_writer_cleanup(route, monkeypatch):
    entered=threading.Event();release=threading.Event();closed=threading.Event()
    class PendingInput:
        def write(self,data):
            entered.set();assert release.wait(2);return len(data)
        def flush(self):pass
        def close(self):closed.set()
    proc=Process();proc.stdin=SimpleNamespace(buffer=PendingInput())
    monkeypatch.setattr(route.subprocess,'Popen',lambda *_a,**_k:proc)
    job=start(route);worker=REAL_THREAD(target=route.workers[0],daemon=True);worker.start()
    try:
        assert entered.wait(1)
        assert not route.JOBS[job]['done'] and not closed.is_set()
    finally:
        release.set();worker.join(2)
    assert not worker.is_alive() and closed.is_set() and route.JOBS[job]['done']


@pytest.mark.parametrize("elapsed,status", [(-1,400),
    (float("nan"),400), (float("inf"),400), (True,400), ("1",400), (None,400)])
def test_frontend_invalid_duration_refuses_before_publication_or_job(route,monkeypatch,elapsed,status):
    published=[]
    monkeypatch.setattr(route,'_publish_case_input_snapshot',lambda *_a,**_k:published.append(True))
    route.request={'meshOnly':True,'meshPreparationElapsedSeconds':elapsed}
    route.handle_run_mesh(None)
    assert route.replies[-1][0] == status
    assert not published and not route.JOBS and not route.workers and not route.spawned
    if status == 408: assert route.replies[-1][1]['code'] == 'mesh_time_budget_exhausted'


@pytest.mark.parametrize("elapsed", [300, 301, 900])
def test_frontend_duration_past_the_target_is_accepted(route,monkeypatch,elapsed):
    # soft 5-minute target (owner decision 2026-10-20): a long browser
    # preparation is timed, never refused
    monkeypatch.setattr(route,'_publish_case_input_snapshot',lambda *_a,**_k:route.input_snapshot)
    route.request={'meshOnly':True,'meshPreparationElapsedSeconds':elapsed}
    route.handle_run_mesh(None)
    assert route.replies[-1][0] == 200


def test_frontend_duration_route_keeps_original_server_origin_and_reduced_cli_deadline(route,monkeypatch):
    observed=[]
    def publish(*_a,**kwargs):
        b=kwargs['mesh_budget'];observed.append(b)
        b.tighten({'meshTimeLimitSeconds':120})
        return route.input_snapshot
    monkeypatch.setattr(route,'_publish_case_input_snapshot',publish)
    route.request={'meshOnly':True,'meshPreparationElapsedSeconds':60}
    route.handle_run_mesh(None)
    assert route.replies[-1][0] == 200
    b=observed[0]
    assert b.deadline == b.started+60
    assert b.remaining() <= 60
    launches=[]
    fake_popen=route.subprocess.Popen # Keep the fixture's existing controlled Process.
    def capture_argv(command, **kwargs):
        launches.append(command)
        return fake_popen(command, **kwargs)
    monkeypatch.setattr(route.subprocess,'Popen',capture_argv)
    route.workers[0]() # Real route worker, existing fake subprocess boundary only.
    assert len(launches)==1 and len(route.spawned)==1
    argv=launches[0]
    assert argv[argv.index('--mesh-started-monotonic')+1]==format(b.started,'.17g')
    assert argv[argv.index('--mesh-deadline-monotonic')+1]==format(b.deadline,'.17g')
