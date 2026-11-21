"""Soft 5-minute target (owner decision 2026-10-20): the tests that
required a mesh to be refused or killed at its deadline were removed with
that behaviour; what remains checks the shared clock and its hand-off.

Execution-budget controls, never a mesh/CFD or browser acceptance test."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import threading
from contextlib import contextmanager
_REAL_THREAD = threading.Thread
from backend.aircooling.runner.mesh_budget import MeshBudget, MeshTimeoutError, mesh_deadline_watchdog
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests.test_case_upload_lock import server_mod, _old_inputs, _Handler, _payload

def _manifest(payload, tag):
    """A cfdrun that declares the batch's STL files, as the original-input
    archive of a mesh request requires."""
    import base64, hashlib
    files = {f"stl_{i}": {"path": name, "contentHash": hashlib.sha256(base64.b64decode(data)).hexdigest()}
             for i, (name, data) in enumerate(sorted(payload["stlFiles"].items()))}
    return json.dumps({"schemaVersion": 4, "opaque": tag, "geometryExport": {"stlFiles": files}})



def budget(seconds):
    now = time.monotonic()
    return MeshBudget(started=now, deadline=now + seconds)


@contextmanager
def owned_execution(r, case_dir):
    # Use the actual OS case lock; the flag exists only for this locked interval.
    from backend.aircooling.runner.case_execution_lock import case_execution_lock
    with case_execution_lock(case_dir, blocking=False):
        r._case_lock_owned = True
        try:
            yield
        finally:
            r._case_lock_owned = False


def runner(tmp_path, seconds=.08):
    r = STLGenRunner.__new__(STLGenRunner)
    r._data = {"meshConfig": {"meshTimeLimitSeconds": seconds}}
    r.mesh_dir = tmp_path / "Mesh"
    r.mesh_dir.mkdir(exist_ok=True)
    r._foam_env_prefix = lambda: ""
    r._start_mesh_budget()
    return r


@pytest.mark.parametrize("started,deadline", [
    (None, 3), (3, None), (float("nan"), 3), (3, float("inf")),
    (-1, 3), (True, 3), (3, 2),
])
def test_invalid_handoff_refuses(started, deadline):
    with pytest.raises(ValueError):
        MeshBudget(started=started, deadline=deadline)


def test_future_origin_and_over_ceiling_refuse():
    now=time.monotonic()
    with pytest.raises(ValueError): MeshBudget(started=now+30, deadline=now+60)
    with pytest.raises(ValueError): MeshBudget(started=now, deadline=now+301)


def test_roundtrip_preserves_actual_monotonic_bits():
    b=budget(42)
    args=b.cli_args()
    child=MeshBudget(started=float(args[1]), deadline=float(args[3]))
    assert child.started.hex()==b.started.hex() and child.deadline.hex()==b.deadline.hex()


def test_repeated_engine_admission_cannot_extend(tmp_path):
    r=runner(tmp_path,1)
    initial=r._mesh_deadline
    time.sleep(.01)
    r._start_mesh_budget()
    assert r._mesh_deadline==initial




def test_larger_config_cannot_extend():
    b=budget(.3)
    first=b.deadline
    b.tighten({"meshTimeLimitSeconds":42})
    b.tighten_manifest({"meshConfig":{"meshTimeLimitSeconds":300},
                        "meshGeometry":{"meshConfig":{"meshTimeLimitSeconds":200}}})
    assert b.deadline==first


def test_completed_budget_cannot_rearm():
    b=budget(1);b.finish()
    with pytest.raises(RuntimeError):b.tighten({})




def test_retirement_disarms_only_after_completion():
    b=budget(.05)
    with mesh_deadline_watchdog(b,cleanup=lambda:None):
        b.finish()
        time.sleep(.07)
    assert not b.active


@pytest.mark.parametrize("route",["handle_run_mesh","handle_prepare_case"])
def test_request_deadline_counts_input_publication_and_transports_origin(server_mod,tmp_path,monkeypatch,route):
    monkeypatch.setattr(server_mod,"CASE_DIR",tmp_path)
    observed=[]
    original=server_mod._publish_case_input_batch
    def slow(*args,**kwargs):
        b=kwargs["mesh_budget"]
        observed.append(b)
        time.sleep(.025)
        return original(*args,**kwargs)
    monkeypatch.setattr(server_mod,"_publish_case_input_batch",slow)
    monkeypatch.setattr(server_mod,"_start_job_worker",lambda fn:None)
    payload=_payload()
    payload["cfdrunJson"]=_manifest(payload,"owned-budget")
    handler=_Handler(payload)
    getattr(server_mod,route)(handler)
    assert handler.status==200,handler.response
    b=observed[0]
    assert b.remaining()<299.975
    if route=="handle_prepare_case":
        command=handler.response["command"]
    else:
        command=server_mod.JOBS[handler.response["jobId"]]["lines"][0]
    assert "--mesh-started-monotonic" in command and "--mesh-deadline-monotonic" in command
    assert format(b.started,".17g") in command and format(b.deadline,".17g") in command










def test_original_quality_refusal_remains_before_solver(tmp_path):
    r=runner(tmp_path,1)
    r._read_cfdrun_manifest=lambda:{}
    def reject():raise RuntimeError("actual quality gate refused")
    r._require_air_mesh_quality=reject
    r._clear_mesh_budget=lambda:pytest.fail("quality rejection reached solver retirement")
    with pytest.raises(RuntimeError,match="actual quality gate refused"):r._run_solve_from_mesh()










@pytest.mark.parametrize("elapsed", [-1, float("nan"), float("inf"), True, "1", None])
def test_frontend_duration_rejects_invalid_execution_metadata(monkeypatch, elapsed):
    import backend.aircooling.runner.mesh_budget as module
    from types import SimpleNamespace
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    b=MeshBudget()
    with pytest.raises(ValueError, match="finite non-negative"):
        b.charge_preparation(elapsed)
    assert b.started == 1000 and b.deadline == 1300




