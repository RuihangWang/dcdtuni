"""Owned OV startup with CPU-only fake processes and local listeners."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[5]
SUPERVISOR = ROOT / "src/frontend/omniverse/cfd_demo/scripts/supervise_kit.py"
UUID = "11111111-1111-1111-1111-111111111111"

def wait_file(path, process=None, timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.is_file(): return path.read_text().strip()
        if process is not None and process.poll() is not None:
            raise AssertionError(f"exited before {path.name}: {process.returncode}")
        time.sleep(.02)
    raise AssertionError(f"missing receipt {path.name}")

def stopped(pid, timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        path=Path(f"/proc/{pid}/status")
        try:
            if "\nState:\tZ" in path.read_text(): return True
        except FileNotFoundError:
            return True
        time.sleep(.02)
    return False

def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1",0))
        return listener.getsockname()[1]

def launch(tmp_path, code, *, port=None, timeout=.5, env=None, extra=()):
    assert tmp_path.is_relative_to(ROOT)
    runtime=tmp_path/"runtime"
    script=tmp_path/"fake_kit.py"
    script.write_text(code)
    command=[sys.executable,str(SUPERVISOR),"--runtime",str(runtime),"--signal-port",
        str(port or free_port()),"--startup-timeout",str(timeout),"--poll-interval",".02",
        "--cleanup-grace",".1",*extra,"--",sys.executable,str(script)]
    output=(tmp_path/"supervisor.log").open("wb")
    process=subprocess.Popen(command,env=env,stdout=output,stderr=subprocess.STDOUT)
    return runtime,process,output

def descendants_code(tmp_path, fatal=False, pid_name="descendant.pid"):
    pid_file=tmp_path/pid_name
    code=("import os, pathlib, subprocess, sys, time\n"
          "child=subprocess.Popen([sys.executable,'-c',"
          "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'],preexec_fn=os.setpgrp)\n"
          f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n")
    if fatal:
        code+=("print('[omni.gpu_foundation_factory.plugin] Failed to create any GPU devices, "
               "including an attempt with compatibility mode.',flush=True)\n")
    return code+"time.sleep(60)\n",pid_file

@pytest.mark.parametrize("fatal",[False,True])
def test_deadline_or_known_fatal_cleans_owned_descendants(tmp_path,fatal):
    code,pid_file=descendants_code(tmp_path,fatal)
    runtime,process,output=launch(tmp_path,code,timeout=.4)
    descendant=int(wait_file(pid_file,process))
    kit=int(wait_file(runtime/"kit.pid",process))
    try:
        assert process.wait(timeout=5)!=0
        failure=wait_file(runtime/"kit.failure")
        assert ("GPU initialization failed" if fatal else "signaling listener deadline") in failure
        assert not (runtime/"kit.ready").exists()
        assert stopped(kit) and stopped(descendant)
        assert (runtime/"kit.exit").is_file()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()

def test_only_owned_kit_listener_is_ready_and_stop_closes_its_session(tmp_path):
    port=free_port()
    code,pid_file=descendants_code(tmp_path)
    code=code.rsplit("time.sleep(60)\n",1)[0]+(
        f"import socket\ns=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();time.sleep(60)\n")
    runtime,process,output=launch(tmp_path,code,port=port,timeout=2)
    descendant=int(wait_file(pid_file,process));kit=int(wait_file(runtime/"kit.pid",process))
    try:
        ready=json.loads(wait_file(runtime/"kit.ready",process))
        assert ready["pid"]==kit and ready["port"]==port
        assert process.poll() is None
        process.terminate();process.wait(timeout=5)
        assert stopped(kit) and stopped(descendant)
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()

def test_foreign_listener_never_counts_as_ready_or_gets_signalled(tmp_path):
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1",0));foreign.listen()
        port=foreign.getsockname()[1]
        runtime,process,output=launch(tmp_path,"import time;time.sleep(60)",port=port)
        try:
            assert process.wait(timeout=5)!=0
            assert "belongs to another process" in wait_file(runtime/"kit.failure")
            assert not (runtime/"kit.ready").exists()
            with socket.create_connection(("127.0.0.1",port),timeout=.2): pass
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=5)
            output.close()

def fake_gpu(tmp_path,hang_probe=False):
    binaries=tmp_path/"bin";binaries.mkdir()
    manifest=tmp_path/"nvidia.json"
    manifest.write_text(json.dumps({"ICD":{"library_path":"libGLX_nvidia.so.0"}}))
    nvidia=binaries/"nvidia-smi"
    if hang_probe:
        probe=tmp_path/"probe.py"
        probe.write_text(descendants_code(tmp_path,pid_name="probe-descendant.pid")[0])
        nvidia.write_text(f"#!/bin/sh\nexec {sys.executable} {probe}\n")
    else:
        nvidia.write_text(f"#!/bin/sh\nprintf '0, GPU-{UUID}, 00000000:01:00.0\\n'\n")
    nvidia.chmod(0o755)
    vulkan=binaries/"vulkaninfo"
    vulkan.write_text(f"#!/bin/sh\nprintf 'GPU3:\\n vendorID = 0x10de\\n deviceUUID = {UUID}\\n'\n")
    vulkan.chmod(0o755)
    return dict(os.environ,PATH=str(binaries)+":"+os.environ["PATH"],
                DC_CFD_VULKAN_ICD=str(manifest))

def test_preflight_is_bounded_and_owned_before_probe_runs(tmp_path):
    env=fake_gpu(tmp_path,hang_probe=True)
    runtime,process,output=launch(tmp_path,"raise AssertionError('must not launch Kit')",env=env,
        extra=("--gpu","0","--preflight-timeout",".2"))
    descendant=int(wait_file(tmp_path/"probe-descendant.pid",process))
    try:
        assert (runtime/"supervisor.port").exists()
        assert process.wait(timeout=5)!=0
        assert "nvidia-smi deadline" in wait_file(runtime/"kit.failure")
        assert not (runtime/"kit.pid").exists()
        assert stopped(descendant)
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()

def test_parent_death_during_preflight_cleans_probe_descendant(tmp_path):
    env=fake_gpu(tmp_path,hang_probe=True);runtime=tmp_path/"runtime"
    parent_script=tmp_path/"parent.py"
    command=[sys.executable,str(SUPERVISOR),"--runtime",str(runtime),"--managed",
        "--signal-port",str(free_port()),"--startup-timeout","5","--preflight-timeout","5",
        "--cleanup-grace",".1","--gpu","0"]
    parent_script.write_text("import os,subprocess,sys,time\n"
        f"cmd={command!r}\n"
        "cmd+=['--parent-pid',str(os.getpid()),'--',sys.executable,'-c','import time;time.sleep(60)']\n"
        "subprocess.Popen(cmd)\ntime.sleep(60)\n")
    with (tmp_path/"parent.log").open("wb") as output:
        parent=subprocess.Popen([sys.executable,str(parent_script)],env=env,
            stdout=output,stderr=subprocess.STDOUT)
        try:
            descendant=int(wait_file(tmp_path/"probe-descendant.pid",parent))
            supervisor=int(wait_file(runtime/"supervisor.pid",parent))
            parent.terminate();parent.wait(timeout=5)
            assert stopped(supervisor) and stopped(descendant)
            assert "startup cancelled" in wait_file(runtime/"kit.failure")
            assert not (runtime/"kit.pid").exists()
        finally:
            if parent.poll() is None:
                parent.terminate();parent.wait(timeout=5)

def test_successful_fake_preflight_preserves_single_uuid_and_one_icd(tmp_path):
    env=fake_gpu(tmp_path);env["EXEC"]="invalid-bypass"
    port=free_port();observed=tmp_path/"observed.json"
    code=("import json,os,pathlib,socket,time\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps({{k:os.environ[k] for k in ['CUDA_VISIBLE_DEVICES','VK_DRIVER_FILES','VK_ICD_FILENAMES','DC_CFD_RESOLVED_ACTIVE_GPU','EXEC'] if k in os.environ}}))\n"
        f"s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();time.sleep(60)\n")
    runtime,process,output=launch(tmp_path,code,port=port,timeout=2,env=env,
        extra=("--gpu","0","--preflight-timeout",".5"))
    try:
        wait_file(runtime/"kit.ready",process);data=json.loads(observed.read_text())
        assert data["CUDA_VISIBLE_DEVICES"]=="GPU-"+UUID
        assert data["VK_DRIVER_FILES"]==data["VK_ICD_FILENAMES"]==env["DC_CFD_VULKAN_ICD"]
        assert data["DC_CFD_RESOLVED_ACTIVE_GPU"]=="3"
        assert "EXEC" not in data
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()


def test_vulkan_probe_timeout_also_cleans_its_descendant(tmp_path):
    env=fake_gpu(tmp_path)
    probe=tmp_path/"vulkan-probe.py"
    probe.write_text(descendants_code(tmp_path,pid_name="vulkan-descendant.pid")[0])
    executable=tmp_path/"bin/vulkaninfo"
    executable.write_text(f"#!/bin/sh\nexec {sys.executable} {probe}\n")
    executable.chmod(0o755)
    runtime,process,output=launch(tmp_path,"raise AssertionError('must not launch Kit')",
        env=env,extra=("--gpu","0","--preflight-timeout",".2"),timeout=2)
    descendant=int(wait_file(tmp_path/"vulkan-descendant.pid",process))
    try:
        assert process.wait(timeout=5)!=0
        assert "vulkaninfo deadline" in wait_file(runtime/"kit.failure")
        assert not (runtime/"kit.pid").exists()
        assert stopped(descendant)
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()


def test_missing_wrapper_fails_inside_owner_before_any_probe(tmp_path):
    env=fake_gpu(tmp_path,hang_probe=True)
    runtime,process,output=launch(tmp_path,"raise AssertionError('must not launch Kit')",
        env=env,extra=("--gpu","0","--required-executable",str(tmp_path/"missing")))
    try:
        assert process.wait(timeout=5)!=0
        assert (runtime/"supervisor.port").exists()
        assert "wrapper is missing" in wait_file(runtime/"kit.failure")
        assert not (tmp_path/"probe-descendant.pid").exists()
        assert not (runtime/"kit.pid").exists()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()


def test_unrelated_process_is_not_part_of_cleanup(tmp_path):
    unrelated=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],
                               start_new_session=True)
    runtime,process,output=launch(tmp_path,"import time;time.sleep(60)",timeout=.15)
    try:
        assert process.wait(timeout=5)!=0
        assert unrelated.poll() is None
        assert (runtime/"kit.failure").exists()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        unrelated.terminate();unrelated.wait(timeout=5)
        output.close()


@pytest.mark.parametrize("ready", [False,True])
def test_launcher_wait_reports_failure_or_owned_readiness(tmp_path,ready):
    start=ROOT/"src/start_server.sh"
    source=start.read_text()
    function=source.split("await_ov_ready() {",1)[1].split("\nstart_backend() {",1)[0]
    runtime=tmp_path/"wait-runtime";runtime.mkdir()
    (runtime/("kit.ready" if ready else "kit.failure")).write_text(
        "owned listener" if ready else "Kit signaling listener deadline exceeded")
    harness=tmp_path/"wait.sh"
    harness.write_text('set -euo pipefail\nawait_ov_ready() {'+function+
        f'\nOV_CURRENT_RUNTIME={str(runtime)!r}\nOV_LAUNCHER_PID=$$\nOV_PORT=18301\n'
        'OV_STARTUP_TIMEOUT=1\nawait_ov_ready\n')
    result=subprocess.run(["bash",str(harness)],capture_output=True,text=True,timeout=3)
    if ready:
        assert result.returncode==0 and "Kit 信令已就绪" in result.stdout
    else:
        assert result.returncode!=0 and "signaling listener deadline" in result.stderr


def test_signal_identity_ignores_transient_process_state(monkeypatch):
    import importlib.util
    sys.path.insert(0,str(SUPERVISOR.parent))
    try:
        spec=importlib.util.spec_from_file_location("owned_ov_supervisor",SUPERVISOR)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SUPERVISOR.parent))
    seen=[]
    identity=(os.getuid(),123,456,"R")
    monkeypatch.setattr(module,"_process_identity",lambda pid:(os.getuid(),123,456,"S"))
    monkeypatch.setattr(module,"_pidfd_open",lambda pid:77)
    monkeypatch.setattr(module.os,"close",lambda fd:None)
    monkeypatch.setattr(module,"_pidfd_send_signal",lambda fd,sig:seen.append((fd,sig)))
    module._signal_member(123,identity,module.signal.SIGTERM)
    assert seen==[(77,module.signal.SIGTERM)]


def test_retry_warning_is_not_the_final_gpu_factory_fatal(tmp_path):
    port=free_port()
    code=("import socket,time\n"
        "print('[Error] [omni.gpu_foundation_factory.plugin] No device could be created. Retrying.',flush=True)\n"
        f"s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();time.sleep(60)\n")
    runtime,process,output=launch(tmp_path,code,port=port,timeout=1)
    try:
        wait_file(runtime/"kit.ready",process)
        assert not (runtime/"kit.failure").exists()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()


def test_kernel_pidfd_path_works_when_python_build_omits_the_wrappers(monkeypatch):
    import importlib.util
    sys.path.insert(0,str(SUPERVISOR.parent))
    try:
        spec=importlib.util.spec_from_file_location("owned_ov_pidfd",SUPERVISOR)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SUPERVISOR.parent))
    child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],
                           start_new_session=True)
    try:
        monkeypatch.delattr(module.os,"pidfd_open",raising=False)
        monkeypatch.delattr(module.signal,"pidfd_send_signal",raising=False)
        identity=module._process_identity(child.pid)
        module._signal_member(child.pid,identity,module.signal.SIGTERM)
        assert child.wait(timeout=3)==-module.signal.SIGTERM
    finally:
        if child.poll() is None:
            child.terminate();child.wait(timeout=3)


def load_supervisor(name):
    import importlib.util
    sys.path.insert(0,str(SUPERVISOR.parent))
    try:
        spec=importlib.util.spec_from_file_location(name,SUPERVISOR)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SUPERVISOR.parent))


def test_cleanup_attempts_all_sessions_and_collects_each_failure(monkeypatch):
    from types import SimpleNamespace
    module=load_supervisor("ov_cleanup_all_sessions")
    owner=module.Owner(.1)
    owner.sessions=[SimpleNamespace(pid=101),SimpleNamespace(pid=102)]
    attempts=[]
    def close(child):
        attempts.append(child.pid)
        if child.pid==102:
            raise OSError(5,"injected process metadata I/O failure")
        owner.sessions.remove(child)
    monkeypatch.setattr(owner,"close",close)
    errors=owner.cleanup()
    assert attempts==[102,101]
    assert len(errors)==1 and "102" in errors[0] and "OSError" in errors[0]


@pytest.mark.parametrize("startup_failure",[False,True])
def test_cleanup_failure_keeps_original_reason_and_publishes_nonzero_exit(tmp_path,monkeypatch,startup_failure):
    from types import SimpleNamespace
    import signal
    module=load_supervisor("ov_cleanup_visible")
    runtime=tmp_path/"cleanup-failure-runtime"
    fake=SimpleNamespace(pid=99999999,poll=lambda:0)
    def spawn(owner,command,**kwargs):
        owner.sessions.append(fake)
        return fake
    def close(owner,child):
        raise OSError(5,"injected owned-session cleanup failure")
    monkeypatch.setattr(module.Owner,"spawn",spawn)
    monkeypatch.setattr(module.Owner,"close",close)
    args=["supervise_kit","--runtime",str(runtime),"--","fake-kit"]
    if startup_failure:
        args[3:3]=["--signal-port",str(free_port())]
    monkeypatch.setattr(sys,"argv",args)
    original_handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        code=module.main()
    finally:
        for sig,handler in original_handlers.items():
            signal.signal(sig,handler)
    assert code!=0 and (runtime/"kit.exit").read_text().strip()!="0"
    assert "owned-session cleanup failure" in (runtime/"cleanup.failure").read_text()
    failure=(runtime/"kit.failure").read_text()
    if startup_failure:
        assert failure.strip()=="Kit exited 0 before its signaling listener"
    else:
        assert "cleanup failed" in failure


def test_proc_disappearance_is_expected_but_unexpected_io_is_not_hidden(monkeypatch):
    module=load_supervisor("ov_proc_errors")
    original=Path.read_text
    def missing(path,*args,**kwargs):
        if str(path)=="/proc/99999999/stat":
            raise ProcessLookupError(3,"process disappeared")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",missing)
    assert module._process_identity(99999999) is None
    def io_error(path,*args,**kwargs):
        if str(path)=="/proc/99999999/stat":
            raise OSError(5,"unexpected process metadata I/O failure")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",io_error)
    with pytest.raises(OSError,match="I/O failure"):
        module._process_identity(99999999)


def test_expired_total_deadline_never_starts_the_next_display_child(tmp_path):
    binaries=tmp_path/"display-bin";binaries.mkdir()
    wait=tmp_path/"display-probe.py"
    wait.write_text("import time;time.sleep(60)\n")
    xdpy=binaries/"xdpyinfo"
    xdpy.write_text(f"#!/bin/sh\nexec {sys.executable} {wait}\n");xdpy.chmod(0o755)
    marker=tmp_path/"xvfb-was-started"
    xvfb_script=tmp_path/"fake-xvfb.py"
    xvfb_script.write_text(f"import pathlib,time;pathlib.Path({str(marker)!r}).write_text('started');time.sleep(60)\n")
    xvfb=binaries/"Xvfb"
    xvfb.write_text(f"#!/bin/sh\nexec {sys.executable} {xvfb_script}\n");xvfb.chmod(0o755)
    env=dict(os.environ,PATH=str(binaries)+":"+os.environ["PATH"])
    runtime,process,output=launch(tmp_path,"raise AssertionError('no Kit after expiry')",
        env=env,timeout=.15,extra=("--display",":fake"))
    try:
        assert process.wait(timeout=5)!=0
        assert "deadline" in (runtime/"kit.failure").read_text()
        assert not marker.exists()
        assert not (runtime/"kit.pid").exists()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()


def test_failure_receipt_write_error_still_attempts_exit_receipt(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import signal
    module=load_supervisor("ov_cleanup_receipt_write")
    runtime=tmp_path/"receipt-failure-runtime"
    fake=SimpleNamespace(pid=99999999,poll=lambda:0)
    def spawn(owner,command,**kwargs):
        owner.sessions.append(fake);return fake
    def close(owner,child):
        raise RuntimeError("injected owned close failure")
    actual_write=module._write;seen=[]
    def write(runtime,name,value):
        seen.append(name)
        if name=="cleanup.failure":
            raise PermissionError(13,"injected one receipt write refusal")
        actual_write(runtime,name,value)
    monkeypatch.setattr(module.Owner,"spawn",spawn)
    monkeypatch.setattr(module.Owner,"close",close)
    monkeypatch.setattr(module,"_write",write)
    monkeypatch.setattr(sys,"argv",["supervise_kit","--runtime",str(runtime),"--","fake-kit"])
    handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        assert module.main()!=0
    finally:
        for sig,handler in handlers.items():signal.signal(sig,handler)
    assert "cleanup.failure" in seen and "kit.failure" in seen and seen[-1]=="kit.exit"
    assert (runtime/"kit.exit").read_text().strip()!="0"
    assert "cleanup failed" in (runtime/"kit.failure").read_text()


def test_cancelled_display_probe_is_never_treated_as_an_unavailable_display(tmp_path,monkeypatch):
    import signal
    module=load_supervisor("ov_display_cancelled")
    runtime=tmp_path/"cancelled-display-runtime"
    calls=[]
    def probe(owner,*args,**kwargs):
        # Explicit cancellation is terminal independently of the stop flag.
        raise module.Cancelled("explicit display cancellation")
    def spawn(owner,command,**kwargs):
        calls.append(command)
        raise RuntimeError("unwanted display child after cancellation")
    monkeypatch.setattr(module.Owner,"probe",probe)
    monkeypatch.setattr(module.Owner,"spawn",spawn)
    monkeypatch.setattr(sys,"argv",["supervise_kit","--runtime",str(runtime),
        "--display",":fake","--","fake-kit"])
    handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        assert module.main()!=0
    finally:
        for sig,handler in handlers.items():signal.signal(sig,handler)
    assert not calls
    assert (runtime/"kit.failure").read_text().strip()=="explicit display cancellation"


def test_term_control_cleanup_closes_kit_descendants_and_owned_display_session(tmp_path):
    binaries=tmp_path/"two-session-bin";binaries.mkdir()
    display_ready=tmp_path/"fake-display-ready"
    display_pid=tmp_path/"fake-display.pid"
    display_script=tmp_path/"fake-display.py"
    display_script.write_text("import os,pathlib,signal,time\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"pathlib.Path({str(display_pid)!r}).write_text(str(os.getpid()))\n"
        f"pathlib.Path({str(display_ready)!r}).write_text('ready')\ntime.sleep(60)\n")
    xvfb=binaries/"Xvfb"
    xvfb.write_text(f"#!/bin/sh\nexec {sys.executable} {display_script}\n");xvfb.chmod(0o755)
    xdpy=binaries/"xdpyinfo"
    xdpy.write_text(f"#!/bin/sh\ntest -f {str(display_ready)!r}\n");xdpy.chmod(0o755)
    env=dict(os.environ,PATH=str(binaries)+":"+os.environ["PATH"])
    port=free_port();code,descendant_file=descendants_code(tmp_path)
    code=code.rsplit("time.sleep(60)\n",1)[0]+(
        f"import socket\ns=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();time.sleep(60)\n")
    runtime,process,output=launch(tmp_path,code,port=port,timeout=3,env=env,
        extra=("--display",":fake"))
    try:
        wait_file(runtime/"kit.ready",process)
        display=int(wait_file(display_pid,process))
        descendant=int(wait_file(descendant_file,process))
        kit=int((runtime/"kit.pid").read_text())
        control=(runtime/"supervisor.port").read_text().strip()
        owner=subprocess.run(["fuser",control+"/tcp"],capture_output=True,text=True)
        assert owner.stdout.strip()==str(process.pid)
        start=time.monotonic()
        subprocess.run(["fuser","-k","-TERM",control+"/tcp"],capture_output=True,check=True)
        assert process.wait(timeout=5)!=0
        assert time.monotonic()-start<5
        assert stopped(kit) and stopped(descendant) and stopped(display)
        assert not (runtime/"cleanup.failure").exists()
        assert "startup cancelled" in (runtime/"kit.failure").read_text()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
        output.close()


def test_display_becoming_false_cannot_launch_after_deadline_crossing(tmp_path,monkeypatch):
    import signal
    module=load_supervisor("ov_display_boundary")
    runtime=tmp_path/"display-boundary-runtime"
    # The display probe returns unavailable just before the deadline; the
    # decision to spawn happens after it. No process or display is launched.
    ticks=iter([0,.1,1.1]);calls=[]
    monkeypatch.setattr(module.time,"monotonic",lambda:next(ticks))
    def probe(owner,*args,**kwargs):
        raise RuntimeError("display unavailable before deadline")
    def spawn(owner,command,**kwargs):
        calls.append(command);raise RuntimeError("late display spawn")
    monkeypatch.setattr(module.Owner,"probe",probe)
    monkeypatch.setattr(module.Owner,"spawn",spawn)
    monkeypatch.setattr(sys,"argv",["supervise_kit","--runtime",str(runtime),
        "--startup-timeout","1","--display",":fake","--","fake-kit"])
    handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        assert module.main()!=0
    finally:
        for sig,handler in handlers.items():signal.signal(sig,handler)
    assert not calls
    assert (runtime/"kit.failure").read_text().strip()=="startup deadline expired before Xvfb launch"



@pytest.mark.parametrize("cwd", ["/", str(ROOT)])
@pytest.mark.parametrize("inherited_cache", [None, "/tmp/legacy-render-textures"])
def test_owned_child_texture_cache_is_absolute_and_overrides_inherited_env(tmp_path, cwd, inherited_cache):
    """Observe the real supervisor child environment, not an assignment regex."""
    runtime = tmp_path / "texture-runtime"
    observed = tmp_path / "texture-environment.json"
    code = ("import json,os,pathlib\n"
            f"pathlib.Path({str(observed)!r}).write_text(json.dumps("
            "{'cache':os.environ.get('DCDTUNI_RENDER_TEXTURE_CACHE')}))\n")
    environment = dict(os.environ)
    environment.pop("DCDTUNI_RENDER_TEXTURE_CACHE", None)
    if inherited_cache is not None:
        environment["DCDTUNI_RENDER_TEXTURE_CACHE"] = inherited_cache
    result = subprocess.run([sys.executable, str(SUPERVISOR), "--runtime", str(runtime), "--",
        sys.executable, "-c", code], cwd=cwd, env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(observed.read_text())["cache"]
    assert actual == str(runtime.resolve() / "render-textures")
    assert Path(actual).is_absolute() and Path(actual).is_relative_to(ROOT)
    assert Path(actual).is_dir()
    assert (runtime / "kit.exit").read_text().strip() == "0"


def test_texture_cache_symlink_escape_rejects_before_child_spawn(tmp_path):
    runtime = tmp_path / "escaped-texture-runtime"
    runtime.mkdir()
    (runtime / "render-textures").symlink_to("/", target_is_directory=True)
    child_marker = tmp_path / "escaped-child-started"
    code = f"from pathlib import Path;Path({str(child_marker)!r}).write_text('unexpected')"
    result = subprocess.run([sys.executable, str(SUPERVISOR), "--runtime", str(runtime), "--",
        sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert not child_marker.exists() and not (runtime / "kit.pid").exists()
    assert "texture cache must stay inside this checkout" in (runtime / "kit.failure").read_text()
    assert (runtime / "kit.exit").read_text().strip() != "0"
