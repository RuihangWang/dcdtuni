"""Dated result directories and the server-session safety rules (no server is started)."""
import importlib.util
import json
import subprocess

import pytest

from agent.scripted_api import rundir, server_session


def test_run_directories_are_dated_and_never_reused(tmp_path):
    first = rundir.create_run_dir(tmp_path, "air", "Single Rack/1")
    second = rundir.create_run_dir(tmp_path, "air", "Single Rack/1")
    assert first != second and first.is_dir() and second.is_dir()
    assert first.name.split("__")[0].endswith("_SGT")
    assert first.name.endswith("__air_Single-Rack-1")
    rundir.write_json(first / "result.json", {"a": 1})
    with pytest.raises(FileExistsError):
        rundir.write_json(first / "result.json", {"a": 2})
    assert json.loads((first / "result.json").read_text()) == {"a": 1}


def test_api_root_is_a_case_log_sibling_without_a_mesh(tmp_path):
    from backend.aircooling.runner.mesh_layout import mesh_exists
    root = rundir.case_api_root(tmp_path)
    assert root == tmp_path / "log" / "api"
    run = rundir.create_run_dir(root, "air")
    assert not mesh_exists(root) and not mesh_exists(run)


def test_free_port_pair_is_bindable():
    port = server_session.free_port_pair()
    assert port % 2 == 0
    assert server_session._port_free(port) and server_session._port_free(port + 1)


def _fake_ports(monkeypatch, holders):
    """``holders``: port -> list of pids still listening; fuser -k -SIG empties a port of mine."""
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["fuser", "-k"]:
            port = int(command[-1].split("/")[0])
            if command[2] == "-KILL" or port not in sticky:
                holders[port] = []
        return subprocess.CompletedProcess(command, 0)
    sticky = set()
    monkeypatch.setattr(server_session, "_port_pids", lambda port: list(holders.get(port, [])))
    monkeypatch.setattr(server_session, "_pid_user", lambda pid: "me" if pid < 200 else "someone-else")
    monkeypatch.setattr(server_session.getpass, "getuser", lambda: "me")
    monkeypatch.setattr(server_session.subprocess, "run", run)
    return calls, sticky


def test_stop_never_signals_a_process_owned_by_someone_else(monkeypatch, tmp_path):
    calls, _ = _fake_ports(monkeypatch, {20000: [111], 20001: [222]})
    session = server_session.ServerSession(tmp_path, tmp_path)
    session.port = 20000
    session.stop()
    assert calls == [["fuser", "-k", "-TERM", "20000/tcp"]]
    assert session.stop_report["refused"] == [{"port": 20001, "owners": {222: "someone-else"}}]
    assert session.stop_report["freed"] == [{"port": 20000, "pids": [111], "signal": "TERM"}]


def test_stop_asks_politely_first_and_kills_only_after_the_grace_period(monkeypatch, tmp_path):
    calls, sticky = _fake_ports(monkeypatch, {20000: [111], 20001: []})
    sticky.add(20000)  # ignores SIGTERM
    session = server_session.ServerSession(tmp_path, tmp_path)
    session.port = 20000
    session.stop(grace=0.3)
    assert calls == [["fuser", "-k", "-TERM", "20000/tcp"], ["fuser", "-k", "-KILL", "20000/tcp"]]
    assert session.stop_report["freed"] == [{"port": 20000, "pids": [111], "signal": "KILL"}]


def test_private_server_defaults_to_the_api_only_app(tmp_path):
    import inspect
    from agent.scripted_api.air import run_air_case
    assert server_session.ServerSession(tmp_path, tmp_path).app == "api"
    assert inspect.signature(run_air_case).parameters["app"].default == "api"


def test_launcher_help_names_the_api_app():
    done = subprocess.run(["bash", str(server_session.LAUNCHER), "--help"], capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 0
    assert "--app api" in done.stdout


def test_private_api_server_serves_no_page_and_stops_on_sigterm(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    session = server_session.ServerSession(case, work)
    session.start()
    port = session.port
    try:
        status, body = session.request("GET", "/")
        assert status == 404 and "--app api" in body["error"]
        info = session.call("GET", "/api/server-info")
        assert info["app"] == "api" and info["caseDir"] == str(case)
        # launched without --gpu: the GPU air solver is not offered, and a request for it is refused
        assert info["gpuSolver"] == {"enabled": False, "devices": None}
        session.login()
        status, body = session.request("POST", "/api/run-mesh", {"caseDir": str(case), "solveOnly": True,
                                                                 "solverEngine": "gpuNewton"})
        assert status == 400 and body["code"] == "solver_engine_invalid" and "--gpu" in body["error"]
    finally:
        session.stop()
    report = session.stop_report
    assert [row["signal"] for row in report["freed"]] == ["TERM"], report
    assert report["launcherReturncode"] == 0, (report, session.log_tail())
    # Nothing holds either port any more (a TIME_WAIT connection may still block a bind for a minute).
    assert server_session._port_pids(port) == [] and server_session._port_pids(port + 1) == []
    assert port not in server_session._RESERVED


def test_the_gpu_flag_of_the_launcher_offers_the_gpu_solver(tmp_path, monkeypatch):
    """start_server.sh --gpu <card>: the server reports the engine as offered on that card (by UUID), whatever
    the caller's environment said."""
    import shutil
    from backend.aircooling.runner import gpu_newton
    if shutil.which("nvidia-smi") is None:
        pytest.skip("no NVIDIA driver on this machine")
    rows = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"], capture_output=True,
                          text=True, timeout=60).stdout.split("\n")
    cards = [tuple(part.strip() for part in row.split(",")) for row in rows if row.strip()]
    if not cards:
        pytest.skip("nvidia-smi lists no card")
    missing = [name for name in gpu_newton.REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        pytest.skip(f"the gpu-solver extra is not installed ({missing})")
    index, uuid = cards[-1]
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER_GPU", "GPU-of-the-caller")
    case = tmp_path / "case"
    case.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    session = server_session.ServerSession(case, work, gpu=index)
    session.start()
    try:
        assert session.command[-2:] == ["--gpu", index]
        info = session.call("GET", "/api/server-info")
        assert info["gpuSolver"] == {"enabled": True, "devices": [uuid]}
    finally:
        session.stop()
    assert "GPU 求解" in session.log_path.read_text(encoding="utf-8")


def test_launcher_is_the_only_server_entry():
    source = (server_session.REPO / "src" / "agent" / "scripted_api" / "server_session.py").read_text()
    assert "start_server.sh" in source and "server.py\"" not in source
    for forbidden in ("pkill", "killall", "os.kill(", ".terminate()", ".kill()"):
        assert forbidden not in source
