"""UDP transport isolation using real kernel sockets and CPU-only owned children.

No Kit/GPU/browser is started. Tests keep all temporary output in this checkout.
"""
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[5]
DEMO = ROOT / "src/frontend/omniverse/cfd_demo"
SUPERVISOR = DEMO / "scripts/supervise_kit.py"
START = ROOT / "src/start_server.sh"
FATAL = "NVST_R_ERROR_UDP_RTP_SOURCE_OPEN_FAILED_NO_PORTS_AVAILABLE"


def wait_file(path, process, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.is_file():
            return path.read_text()
        if process.poll() is not None:
            raise AssertionError(f"exit {process.returncode} before {path.name}")
        time.sleep(.01)
    raise AssertionError(f"missing {path.name}")


def launch(tmp_path, code, *, stream=0, signal=0, extra=(), env=None):
    assert tmp_path.resolve().is_relative_to(ROOT)
    runtime = tmp_path / "runtime"
    script = tmp_path / "child.py"
    script.write_text(code)
    command = [sys.executable, str(SUPERVISOR), "--runtime", str(runtime),
        "--stream-port", str(stream), "--signal-port", str(signal),
        "--startup-timeout", "5", "--cleanup-grace", ".1", "--poll-interval", ".01",
        *extra, "--", sys.executable, str(script)]
    output = (tmp_path / "supervisor.log").open("wb")
    return runtime, subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT), output


def stop(process, output):
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
    finally:
        output.close()


def bind_child_code(marker):
    # The child binds after preflight, proving no probe leaked/reserved the port.
    return ("import json, os, pathlib, socket\n"
        "p=int(os.environ['DC_CFD_STREAM_PORT'])\n"
        "a=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);a.bind(('0.0.0.0',p))\n"
        "b=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)\n"
        "b.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);b.bind(('::',p))\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps([a.getsockname()[1],b.getsockname()[1]]))\n")


@pytest.mark.parametrize("family,address,v6only,expected", [
    (socket.AF_INET, "127.0.0.1", None, "IPv4"),
    (socket.AF_INET6, "::1", 1, "IPv6"),
    (socket.AF_INET6, "::", 0, "IPv4"),
])
def test_occupied_udp_refuses_before_child_and_preserves_foreign_socket(tmp_path, family, address, v6only, expected):
    with socket.socket(family, socket.SOCK_DGRAM) as foreign:
        if v6only is not None:
            foreign.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
        foreign.bind((address, 0))
        port = foreign.getsockname()[1]
        marker = tmp_path / "spawned"
        runtime, process, output = launch(tmp_path, f"from pathlib import Path;Path({str(marker)!r}).touch()", stream=port)
        try:
            assert process.wait(timeout=5) == 1
            failure = (runtime / "kit.failure").read_text()
            assert f"UDP stream port {port} unavailable on {expected}" in failure
            assert not marker.exists() and not (runtime / "kit.pid").exists()
            assert not (runtime / "kit.ready").exists()
            # Occupancy is checked, never stolen or signalled.
            foreign.settimeout(1)
            destination = ("127.0.0.1", port) if family == socket.AF_INET else ("::1", port)
            foreign.sendto(b"still-owned", destination)
            assert foreign.recvfrom(64)[0] == b"still-owned"
        finally:
            stop(process, output)


def test_released_udp_port_is_available_to_both_child_families(tmp_path):
    with socket.socket(type=socket.SOCK_DGRAM) as prior:
        prior.bind(("127.0.0.1", 0))
        port = prior.getsockname()[1]
    marker = tmp_path / "bound.json"
    runtime, process, output = launch(tmp_path, bind_child_code(marker), stream=port)
    try:
        assert process.wait(timeout=5) == 0
        assert json.loads(marker.read_text()) == [port, port]
        assert not (runtime / "kit.failure").exists()
        # The owned child exit also releases transport sockets.
        with socket.socket(type=socket.SOCK_DGRAM) as probe:
            probe.bind(("0.0.0.0", port))
    finally:
        stop(process, output)


def test_same_numeric_tcp_port_does_not_block_independent_udp(tmp_path):
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0)); foreign.listen()
        port = foreign.getsockname()[1]
        marker = tmp_path / "bound.json"
        runtime, process, output = launch(tmp_path, bind_child_code(marker), stream=port)
        try:
            assert process.wait(timeout=5) == 0
            assert json.loads(marker.read_text()) == [port, port]
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                accepted, _ = foreign.accept(); accepted.close()
        finally:
            stop(process, output)


def test_port_taken_during_display_preflight_is_rechecked_before_spawn(tmp_path):
    binaries = tmp_path / "bin"; binaries.mkdir()
    started, resume = tmp_path / "probe-started", tmp_path / "resume"
    probe = binaries / "xdpyinfo"
    probe.write_text(f"#!{sys.executable}\nimport pathlib,time\n"
        f"pathlib.Path({str(started)!r}).touch()\n"
        f"while not pathlib.Path({str(resume)!r}).exists(): time.sleep(.01)\n")
    probe.chmod(0o755)
    with socket.socket(type=socket.SOCK_DGRAM) as selector:
        selector.bind(("127.0.0.1", 0)); port = selector.getsockname()[1]
    marker = tmp_path / "spawned"
    env = dict(os.environ, PATH=str(binaries)+":"+os.environ["PATH"])
    runtime, process, output = launch(tmp_path, f"from pathlib import Path;Path({str(marker)!r}).touch()",
        stream=port, extra=("--display", ":777"), env=env)
    try:
        wait_file(started, process)
        with socket.socket(type=socket.SOCK_DGRAM) as foreign:
            foreign.bind(("127.0.0.1", port)); resume.touch()
            assert process.wait(timeout=5) == 1
            assert f"UDP stream port {port} unavailable on IPv4" in (runtime / "kit.failure").read_text()
            assert not marker.exists() and not (runtime / "kit.pid").exists()
            assert foreign.getsockname()[1] == port
    finally:
        resume.touch(); stop(process, output)


@pytest.mark.parametrize("split,internal", [(False, False), (True, True)])
def test_real_udp_bind_failure_log_is_fatal_even_with_owned_signaling(tmp_path, split, internal):
    # Legacy stream-port=0 exercises fatal monitoring independently of preflight.
    # The marker is produced only after a real EADDRINUSE from the kernel.
    with socket.socket(type=socket.SOCK_DGRAM) as foreign, socket.socket() as selector:
        foreign.bind(("127.0.0.1", 0)); port = foreign.getsockname()[1]
        selector.bind(("127.0.0.1", 0)); signal_port = selector.getsockname()[1]
    foreign = socket.socket(type=socket.SOCK_DGRAM); foreign.bind(("127.0.0.1", port))
    code = ("import errno, os, pathlib, socket, time\n"
        f"tcp=socket.socket();tcp.bind(('127.0.0.1',{signal_port}));tcp.listen()\n"
        f"udp=socket.socket(type=socket.SOCK_DGRAM)\ntry: udp.bind(('0.0.0.0',{port}))\n"
        "except OSError as error:\n"
        " assert error.errno==errno.EADDRINUSE\n")
    if internal:
        code += " log=pathlib.Path(os.environ['TMPDIR']).parent/'kit-internal.log'\n"
        code += f" log.write_text({FATAL[:24]!r})\n time.sleep(.04)\n"
        code += f" with log.open('a') as out: out.write({FATAL[24:]!r})\n"
    else:
        code += f" print({FATAL!r},flush=True)\n"
    code += "time.sleep(60)\n"
    runtime, process, output = launch(tmp_path, code, signal=signal_port)
    try:
        assert process.wait(timeout=2) == 1
        assert "Kit UDP stream initialization failed" in (runtime / "kit.failure").read_text()
        if (runtime / "kit.ready").exists():
            # A prior TCP receipt is explicit about its limited meaning.
            assert json.loads((runtime / "kit.ready").read_text())["readiness"] == "signaling"
        assert foreign.getsockname()[1] == port
    finally:
        stop(process, output); foreign.close()


@pytest.mark.parametrize("value", ["-1", "65536", "999999999999999999999", "false", "1.5"])
def test_supervisor_invalid_stream_argument_is_rejected_before_spawn(tmp_path, value):
    result = subprocess.run([sys.executable, str(SUPERVISOR), "--runtime", str(tmp_path / "runtime"),
        "--stream-port", value, "--", sys.executable, "-c", "raise AssertionError('spawned')"],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "stream" in result.stderr
    assert not (tmp_path / "runtime/kit.pid").exists()


@pytest.mark.parametrize("value", ["", "0", "-1", "65536", "00065536", "1.5", "false", " 42", "9"*100])
def test_demo_invalid_stream_environment_is_rejected_before_runtime(tmp_path, value):
    runtime = tmp_path / "runtime"
    env = dict(os.environ, DC_CFD_STREAM_PORT=value, DC_CFD_RUNTIME_DIR=str(runtime))
    result = subprocess.run(["bash", str(DEMO / "scripts/start_demo.sh")], env=env,
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "UDP stream port" in result.stderr
    assert not runtime.exists()


@pytest.mark.parametrize("equals", [False, True])
@pytest.mark.parametrize("value", ["", "0", "-1", "65536", "00065536", "1.5", "false", " 42", "9"*100])
def test_launcher_invalid_stream_argument_is_rejected_before_gpu_or_key(tmp_path, equals, value):
    flag = [f"--ov-stream-port={value}"] if equals else ["--ov-stream-port", value]
    result = subprocess.run(["bash", str(START), "--ov", *flag, "--no-backend"],
        capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and "--ov-stream-port" in result.stderr
    assert "显示授权密钥" not in result.stderr


def demo_command(tmp_path, *, stream=None, signal="8302"):
    """Run the actual supervised-child launcher under its actual CPU owner.

    The executable is a CPU shim that records argv then exits. GPU selection is
    supplied as a controlled fixture, without calling a physical GPU probe.
    """
    runtime, build = tmp_path / "runtime", tmp_path / "build"
    build.mkdir(); observed = tmp_path / "argv.json"
    shim = build / "omni.cae_streaming.kit.sh"
    shim.write_text(f"#!{sys.executable}\nimport json,pathlib,sys\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps(sys.argv[1:]))\n")
    shim.chmod(0o755)
    icd = tmp_path / "icd.json"; icd.write_text("{}")
    env = dict(os.environ, DC_CFD_RUNTIME_DIR=str(runtime), DC_CFD_KIT_BUILD=str(build),
        DC_CFD_GPU="0", DC_CFD_RESOLVED_GPU="0", DC_CFD_RESOLVED_UUID="GPU-controlled-fixture",
        DC_CFD_RESOLVED_ACTIVE_GPU="0", DC_CFD_RESOLVED_ICD=str(icd),
        DC_CFD_VTK_SITE=str(tmp_path), DC_CFD_SIGNAL_PORT=signal)
    env.pop("DC_CFD_STREAM_PORT", None)
    if stream is not None:
        env["DC_CFD_STREAM_PORT"] = stream
    result = subprocess.run([sys.executable, str(SUPERVISOR), "--runtime", str(runtime), "--",
        "bash", str(DEMO / "scripts/start_demo.sh"), "--supervised-child"], env=env,
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, (runtime / "kit.log").read_text()
    return json.loads(observed.read_text())


@pytest.mark.parametrize("value,expected", [(None, "47998"), ("1", "1"), ("65535", "65535"), ("0000042", "42")])
def test_demo_default_and_explicit_stream_are_lowered_to_actual_kit_argv(tmp_path, value, expected):
    args = demo_command(tmp_path, stream=value)
    assert f"--/exts/omni.kit.livestream.app/primaryStream/streamPort={expected}" in args
    assert "--/exts/omni.kit.livestream.app/primaryStream/signalPort=8302" in args


def test_same_signaling_configuration_can_construct_distinct_stream_ports(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    a, b = demo_command(first, stream="47998"), demo_command(second, stream="47999")
    signal = "--/exts/omni.kit.livestream.app/primaryStream/signalPort=8302"
    assert signal in a and signal in b
    assert "--/exts/omni.kit.livestream.app/primaryStream/streamPort=47998" in a
    assert "--/exts/omni.kit.livestream.app/primaryStream/streamPort=47999" in b


@pytest.mark.parametrize("family,address", [(socket.AF_INET,"127.0.0.1"), (socket.AF_INET6,"::1")])
@pytest.mark.parametrize("managed", ["0", "1"])
def test_demo_occupied_udp_refuses_synchronously_before_background_gpu_work(tmp_path, family, address, managed):
    with socket.socket(family, socket.SOCK_DGRAM) as foreign:
        if family == socket.AF_INET6:
            foreign.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
        foreign.bind((address,0)); port=foreign.getsockname()[1]
        runtime=tmp_path/"runtime"
        env=dict(os.environ,DC_CFD_STREAM_PORT=str(port),DC_CFD_RUNTIME_DIR=str(runtime),DC_CFD_MANAGED=managed)
        result=subprocess.run(["bash",str(DEMO/"scripts/start_demo.sh")],env=env,
            capture_output=True,text=True,timeout=5)
        assert result.returncode==1 and f"UDP stream port {port} unavailable" in result.stderr
        assert not (runtime/"supervisor.pid").exists() and not (runtime/"kit.pid").exists()
        assert "Supervisor PID" not in result.stdout
        assert foreign.getsockname()[1]==port
