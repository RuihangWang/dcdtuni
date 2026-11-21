"""Real HTTP routing contracts for liquid runtime sessions."""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from backend.tests.test_liquid.test_runtime import runtime_config


SERVER_DIR = Path(__file__).resolve().parents[3] / "server"


def _server_module():
    """server.py under its own name: the bare name ``server`` may already be
    bound to the src/server package by an earlier test in the session."""
    name = "liquid_runtime_server_under_test"
    if name not in sys.modules:
        if str(SERVER_DIR) not in sys.path:
            sys.path.insert(0, str(SERVER_DIR))
        spec = importlib.util.spec_from_file_location(name, SERVER_DIR / "server.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class ServerHarness:
    def __init__(self, tmp_path):
        self.module = _server_module()
        # The server fails closed without an auth store, so the harness runs a
        # real one and sends an administrator session cookie.
        from auth_manager import AuthManager
        import secrets
        self._saved = (self.module.AUTH_MGR, self.module.CASE_DIR)
        user, password = "liquid_" + secrets.token_hex(4), secrets.token_urlsafe(32)
        self.module.AUTH_MGR = AuthManager(tmp_path / "auth", log_path=tmp_path / "auth.log",
                                           admin_credentials=(user, password))
        self.token = self.module.AUTH_MGR.create_session(user, "admin", "127.0.0.1")
        self.module.CASE_DIR = tmp_path

        class QuietHandler(self.module.STLServerHandler):
            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.cookie = f"{self.module._auth_cookie_name_for_port(self.server.server_port)}={self.token}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.module.AUTH_MGR, self.module.CASE_DIR = self._saved

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Cookie": self.cookie},
        )
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())
        return response.status, json.loads(response.read())


@pytest.fixture
def http_server(tmp_path):
    harness = ServerHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def test_http_create_step_frames_result_and_checkpoint(http_server):
    """Breaking any public route/status contract must fail against the real handler."""
    payload = {
        "liquidrun": runtime_config(samples=3).model_dump(by_alias=True),
        "project_revision": 12,
    }
    code, created = http_server.request("POST", "/api/liquid/runs", payload)
    assert code == 201
    assert created["status"] == "ready"
    run_id = created["run_id"]

    assert http_server.request("GET", f"/api/liquid/runs/{run_id}")[0] == 200
    code, stepped = http_server.request("POST", f"/api/liquid/runs/{run_id}/step", {})
    assert code == 200
    assert stepped["frame"]["seq"] == 1
    code, frames = http_server.request(
        "GET", f"/api/liquid/runs/{run_id}/frames?after=0"
    )
    assert code == 200
    assert [frame["seq"] for frame in frames["frames"]] == [1]
    assert http_server.request(
        "GET", f"/api/liquid/runs/{run_id}/checkpoint"
    )[1]["seq"] == 1
    assert http_server.request(
        "GET", f"/api/liquid/runs/{run_id}/result"
    )[1]["status"] == "ready"



def test_http_start_pause_resume_and_cancel(http_server):
    """Lifecycle POST routing must retain the server worker across requests."""
    code, created = http_server.request("POST", "/api/liquid/runs", {
        "liquidrun": runtime_config(samples=40).model_dump(by_alias=True)
    })
    assert code == 201
    run_id = created["run_id"]

    code, started = http_server.request(
        "POST", f"/api/liquid/runs/{run_id}/start", {"speed": 1.0}
    )
    assert code == 200
    assert started["status"] == "running"
    code, paused = http_server.request(
        "POST", f"/api/liquid/runs/{run_id}/pause", {}
    )
    assert code == 200
    assert paused["status"] == "paused"
    assert http_server.request(
        "POST", f"/api/liquid/runs/{run_id}/resume", {"speed": 1.0}
    )[1]["status"] == "running"
    assert http_server.request(
        "POST", f"/api/liquid/runs/{run_id}/cancel", {}
    )[1]["status"] == "cancelled"

def test_http_errors_use_400_404_and_409_json(http_server):
    """Falling through to HTML errors or stack traces must fail these JSON checks."""
    code, error = http_server.request("POST", "/api/liquid/runs", {"liquidrun": {}})
    assert code == 400
    assert error["error"]

    code, error = http_server.request("GET", "/api/liquid/runs/does-not-exist")
    assert code == 404
    assert error["error"]

    code, created = http_server.request("POST", "/api/liquid/runs", {
        "liquidrun": runtime_config(samples=2).model_dump(by_alias=True)
    })
    assert code == 201
    code, error = http_server.request(
        "POST", f"/api/liquid/runs/{created['run_id']}/resume", {}
    )
    assert code == 409
    assert "ready" in error["error"]


def test_http_creation_captures_case_directory(http_server, tmp_path):
    """Reading mutable CASE_DIR again after create must move artifacts and fail."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    http_server.module.CASE_DIR = first
    code, created = http_server.request("POST", "/api/liquid/runs", {
        "liquidrun": runtime_config(samples=2).model_dump(by_alias=True)
    })
    assert code == 201
    http_server.module.CASE_DIR = second
    http_server.request("POST", f"/api/liquid/runs/{created['run_id']}/step", {})

    assert (first / "Liquid" / "log" / f"run-{created['run_id']}" / "result.json").is_file()
    assert not (second / "Liquid" / "log" / f"run-{created['run_id']}").exists()


def test_air_only_server_import_does_not_eagerly_import_torch():
    """Adding a top-level runtime import in server.py must fail this process check."""
    source_root = SERVER_DIR.parent
    program = (
        "import sys;"
        f"sys.path.insert(0,{str(SERVER_DIR)!r});"
        f"sys.path.insert(0,{str(source_root)!r});"
        "import server;"
        "print('yes' if 'torch' in sys.modules else 'no')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        text=True, capture_output=True, check=True, cwd=source_root.parent,
    )
    assert completed.stdout.strip().splitlines()[-1] == "no"


def test_dae_http_heat_change_equilibrium_and_trajectory_are_distinct(http_server):
    from backend.tests.test_liquid.test_dae_runtime import case
    code,created=http_server.request('POST','/api/liquid/runs',{'liquidrun':case()})
    assert code==201,created
    path=f"/api/liquid/runs/{created['run_id']}"
    code,pending=http_server.request('POST',path+'/inputs',{'heat_multiplier':1.2})
    assert code==200 and pending['frame']==created['frame']
    code,step=http_server.request('POST',path+'/step',{})
    assert code==200 and step['frame']['heat_multiplier']==1.2
    code,equilibrium=http_server.request('POST',path+'/equilibrium',{})
    assert code==200 and equilibrium['kind']=='equilibrium'
    assert http_server.request('GET',path)[1]['frame']==step['frame']
    code,error=http_server.request('POST',path+'/inputs',{'heat_multiplier':1.2,'surprise':True})
    assert code==400 and error['error']
    code,error=http_server.request('POST',path+'/inputs',{'heat_multiplier':-1})
    assert code==400 and error['error']


def test_dae_http_combines_queued_heat_and_pump_inputs_in_the_accepted_manifest(http_server):
    from backend.tests.test_liquid.test_dae_runtime import case
    code,created=http_server.request('POST','/api/liquid/runs',{'liquidrun':case()})
    assert code==201,created
    path=f"/api/liquid/runs/{created['run_id']}"
    code,pending=http_server.request('POST',path+'/inputs',{
        'heat_multiplier':1.2,'pump_rpm_ratios':{'pump':.8}})
    assert code==200,pending
    assert pending['frame']==created['frame']
    code,step=http_server.request('POST',path+'/step',{})
    assert code==200,step
    assert step['frame']['seq']==1 and step['frame']['heat_multiplier']==1.2
    code,accepted=http_server.request('GET',path+'/executed-input')
    assert code==200,accepted
    pump=next(edge for edge in accepted['edges'] if edge['id']=='pump')
    plate=next(edge for edge in accepted['edges'] if edge['type']=='cold_plate')
    assert pump['params']['rpm_ratio']==[1.,.8]
    assert plate['params']['Q_W']==[500.,600.]
    for invalid in ({'pump_rpm_ratios':{'absent':.8}}, {}, {'pump_rpm_ratios':{'pump':.8},'unexpected':True}):
        code,error=http_server.request('POST',path+'/inputs',invalid)
        assert code==400 and error['error']


def test_dae_http_positive_valve_input_applies_only_to_the_next_accepted_step(http_server):
    from backend.tests.test_liquid.test_dae_live_actuators import controllable_case
    code,created=http_server.request('POST','/api/liquid/runs',{'liquidrun':controllable_case()})
    assert code==201,created
    path=f"/api/liquid/runs/{created['run_id']}"
    code,pending=http_server.request('POST',path+'/inputs',{'valve_openings':{'branch_valve':.7}})
    assert code==200,pending
    assert pending['frame']==created['frame']
    code,accepted=http_server.request('GET',path+'/executed-input')
    assert code==200,accepted
    valve=next(edge for edge in accepted['edges'] if edge['id']=='branch_valve')
    assert valve['params']['opening']==[1.]
    code,step=http_server.request('POST',path+'/step',{})
    assert code==200,step
    code,accepted=http_server.request('GET',path+'/executed-input')
    assert code==200,accepted
    valve=next(edge for edge in accepted['edges'] if edge['id']=='branch_valve')
    assert valve['params']['opening']==[1.,.7]
    code,error=http_server.request('POST',path+'/inputs',{'valve_openings':{'branch_valve':0}})
    assert code==400 and error['error']


def test_dae_http_heat_audit_is_bound_to_the_project_and_accepted_steps(http_server):
    from backend.tests.test_liquid.test_hybrid_heat_audit import coupled_case
    code,created=http_server.request('POST','/api/liquid/runs',{'liquidrun':coupled_case()})
    assert code==201,created
    path=f"/api/liquid/runs/{created['run_id']}"
    assert http_server.request('POST',path+'/step',{})[0]==200
    code,audit=http_server.request('GET',path+'/heat-audit?project_uid=project-stable-uid')
    assert code==200,audit
    assert audit['project_uid']=='project-stable-uid'
    assert audit['liquid_run_id']==created['run_id']
    assert audit['audited_server_count']==1 and audit['checked_server_samples']==2
    assert audit['identity_verified'] is True and audit['handoff_ready'] is False
    assert audit['max_abs_closure_residual_W']<1e-6
    code,error=http_server.request('GET',path+'/heat-audit?project_uid=another-project')
    assert code==400 and 'project_uid' in error['error']


def test_render_scene_export_route_writes_only_current_case_and_rejects_case_switch(http_server):
    scene={'schema':'dcdtuni.render-scene/v1','project':{'uid':'same-project','name':'Shinaide'},
        'coordinateSystem':{'upAxis':'Y','metersPerUnit':1,'handedness':'right'},
        'primitives':[],'textures':{},'view':{'selectedUids':[],'timeSeconds':600.},'warnings':[]}
    selected=str(http_server.module.CASE_DIR)
    code,receipt=http_server.request('POST','/api/render-scene/export',{'scene':scene,'case_dir':selected})
    assert code==201,receipt
    path=Path(receipt['path'])
    assert path.is_relative_to(Path(selected)/'Render'/'scenes')
    assert json.loads(path.read_text())==scene
    assert receipt['project_uid']=='same-project' and receipt['time_seconds']==600.
    code,error=http_server.request('POST','/api/render-scene/export',{'scene':scene,'case_dir':selected+'/different'})
    assert code==400 and error['error']
    broken={**scene,'unexpected':True}
    code,error=http_server.request('POST','/api/render-scene/export',{'scene':broken,'case_dir':selected})
    assert code==400 and error['error']


def test_http_accepted_input_export_excludes_pending_commands_and_equilibrium(http_server):
    from backend.tests.test_liquid.test_dae_runtime import case
    code,created=http_server.request('POST','/api/liquid/runs',{'liquidrun':case()})
    assert code==201,created
    path=f"/api/liquid/runs/{created['run_id']}"
    assert http_server.request('POST',path+'/inputs',{'heat_multiplier':1.2})[0]==200
    assert http_server.request('POST',path+'/step',{})[0]==200
    before=http_server.request('GET',path)[1]['frame']
    assert http_server.request('POST',path+'/inputs',{'heat_multiplier':3.})[0]==200
    assert http_server.request('POST',path+'/equilibrium',{'heat_multiplier':3.})[0]==200
    code,executed=http_server.request('GET',path+'/executed-input')
    assert code==200,executed
    assert executed['time']['N_t']==2 and executed['events']==[]
    assert next(e for e in executed['edges'] if e['type']=='cold_plate')['params']['Q_W']==[500.,600.]
    assert http_server.request('GET',path)[1]['frame']==before
    from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV3
    LiquidrunDAEV3.model_validate(executed)
