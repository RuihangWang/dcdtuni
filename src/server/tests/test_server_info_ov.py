"""CPU-only /api/server-info OV signalling defaults and safe env parsing."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

_SERVER_FILE = Path(__file__).resolve().parent.parent / "server.py"
_SPEC = importlib.util.spec_from_file_location("dcdtuni_server_info_under_test", _SERVER_FILE)
server = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = server
_SPEC.loader.exec_module(server)


def _server_info(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "_send_json", lambda handler, status, data: sent.append((status, data)))
    monkeypatch.setattr(server, "CASE_DIR", None)
    server.handle_server_info(object())
    assert len(sent) == 1
    status, data = sent[0]
    assert status == 200
    return data


def test_server_info_exposes_configured_dns_host_and_default_port(monkeypatch):
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_HOST", "40901.dynip.ntu.edu.sg")
    monkeypatch.delenv("DCDTUNI_OV_SIGNALING_PORT", raising=False)
    info = _server_info(monkeypatch)
    assert info["ovSignalingHost"] == "40901.dynip.ntu.edu.sg"
    assert info["ovSignalingPort"] == 8301


@pytest.mark.parametrize("host", ["localhost", "edge-node.example.org", "192.0.2.4"])
def test_server_info_accepts_simple_dns_or_ipv4_host(monkeypatch, host):
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_HOST", host)
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_PORT", "8456")
    info = _server_info(monkeypatch)
    assert info["ovSignalingHost"] == host
    assert info["ovSignalingPort"] == 8456


@pytest.mark.parametrize("host", [
    "", " ", "https://40901.dynip.ntu.edu.sg", "40901.dynip.ntu.edu.sg/path",
    "40901.dynip.ntu.edu.sg:8301", "user@host.example", "host.example\nX-Injected: yes",
    "-bad.example", "bad-.example", "a..example", "例子.example",
    "a" * 64 + ".example",
])
def test_server_info_omits_unsafe_host_values(monkeypatch, host):
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_HOST", host)
    info = _server_info(monkeypatch)
    assert info.get("ovSignalingHost") is None


@pytest.mark.parametrize("port", ["0", "65536", "-1", "8301/path", " 8301 ", "x", "01"])
def test_server_info_rejects_bad_port_and_uses_safe_default(monkeypatch, port):
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_HOST", "40901.dynip.ntu.edu.sg")
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_PORT", port)
    info = _server_info(monkeypatch)
    assert info["ovSignalingHost"] == "40901.dynip.ntu.edu.sg"
    assert info["ovSignalingPort"] == 8301


def test_server_info_has_no_ov_host_without_environment_value(monkeypatch):
    monkeypatch.delenv("DCDTUNI_OV_SIGNALING_HOST", raising=False)
    monkeypatch.delenv("DCDTUNI_OV_SIGNALING_PORT", raising=False)
    info = _server_info(monkeypatch)
    assert info["ovSignalingHost"] is None
    assert info["ovSignalingPort"] == 8301


def test_server_info_reports_whether_this_launch_started_the_ov_bridge(monkeypatch):
    monkeypatch.delenv("DCDTUNI_OV_GRANT_KEY", raising=False)
    assert _server_info(monkeypatch)["ovBridge"] is False
    monkeypatch.setenv("DCDTUNI_OV_GRANT_KEY", "ab" * 32)
    info = _server_info(monkeypatch)
    assert info["ovBridge"] is True
    assert "ab" * 32 not in repr(info)


def test_server_info_reports_whether_this_launch_offers_the_gpu_solver(monkeypatch):
    monkeypatch.delenv("DCDTUNI_GPU_SOLVER", raising=False)
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER_GPU", "GPU-a")
    assert _server_info(monkeypatch)["gpuSolver"] == {"enabled": False, "devices": None}
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER", "1")
    assert _server_info(monkeypatch)["gpuSolver"] == {"enabled": True, "devices": ["GPU-a"]}
    monkeypatch.delenv("DCDTUNI_GPU_SOLVER_GPU")
    assert _server_info(monkeypatch)["gpuSolver"] == {"enabled": True, "devices": None}


def _gpu_solver_reply(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "_send_json", lambda handler, status, data: sent.append((status, data)))
    server.handle_gpu_solver_status(object())
    assert len(sent) == 1 and sent[0][0] == 200
    return sent[0][1]


def test_gpu_solver_status_lists_the_cards_and_the_memory_of_the_case_mesh(monkeypatch, tmp_path):
    import subprocess
    from backend.aircooling.runner import gpu_newton
    poly = tmp_path / "Mesh" / "processor0" / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "neighbour", "boundary"):
        (poly / name).write_text("x\n")
    (poly / "owner").write_text('FoamFile\n{\n    note "nPoints:9 nCells:1200000 nFaces:30 nInternalFaces:9";\n}\n')
    smi = "0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 20000, 97\n1, GPU-b, NVIDIA GeForce RTX 4090, 24564, 500, 3\n"
    monkeypatch.setattr(gpu_newton.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=smi, stderr=""))
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    monkeypatch.delenv("DCDTUNI_GPU_SOLVER", raising=False)
    assert _gpu_solver_reply(monkeypatch) == {"enabled": False, "devices": None, "cards": [], "mesh": None,
                                             "message": None}
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER", "1")
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER_GPU", "GPU-a,GPU-b")
    reply = _gpu_solver_reply(monkeypatch)
    assert [(c["index"], c["freeMiB"], c["utilization"]) for c in reply["cards"]] == [("0", 4564, 97), ("1", 24064, 3)]
    assert reply["mesh"] == {"cells": 1_200_000, "estimateMiB": 22_400, "bestFreeMiB": 24_064, "fits": True}
    assert reply["message"] is None
    monkeypatch.setenv("DCDTUNI_GPU_SOLVER_GPU", "GPU-a")
    reply = _gpu_solver_reply(monkeypatch)
    assert reply["mesh"]["fits"] is False and "start_server.sh" in reply["message"] and "--gpu" in reply["message"]
    monkeypatch.setattr(server, "CASE_DIR", None)
    assert _gpu_solver_reply(monkeypatch)["mesh"]["cells"] is None


def _proc_tcp(tmp_path, rows):
    head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (tmp_path / "tcp").write_text(head + "".join(
        f"   0: 00000000:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 00000000  1002        0 1\n"
        for port, state in rows))
    (tmp_path / "tcp6").write_text(head)
    return str(tmp_path)


def test_local_listening_reads_proc_without_connecting(tmp_path):
    root = _proc_tcp(tmp_path, [(8302, "0A"), (8303, "01")])
    assert server._local_tcp_listening(8302, root) is True
    assert server._local_tcp_listening(8303, root) is False   # established, not listening
    assert server._local_tcp_listening(8301, root) is False
    assert server._local_tcp_listening(8302, str(tmp_path / "missing")) is None


def test_ov_status_reports_bridge_and_kit_readiness(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(server, "_send_json", lambda handler, status, data: sent.append(data))
    monkeypatch.setenv("DCDTUNI_OV_SIGNALING_PORT", "8302")
    monkeypatch.delenv("DCDTUNI_OV_GRANT_KEY", raising=False)
    server.handle_ov_status(object())
    assert sent[-1] == {"bridge": False, "port": 8302, "host": None, "kitListening": False}
    monkeypatch.setenv("DCDTUNI_OV_GRANT_KEY", "k")
    monkeypatch.setattr(server, "_local_tcp_listening", lambda port: port == 8302)
    server.handle_ov_status(object())
    assert sent[-1]["bridge"] and sent[-1]["kitListening"]
