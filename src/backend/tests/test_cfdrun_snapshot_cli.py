"""CLI delegates an owned stdin snapshot without launching CFD in these tests."""
import hashlib
import io
import sys
from types import SimpleNamespace
import pytest
from backend.aircooling.runner import run_cfd as cli
from backend.aircooling.runner import stlgen_runner as implementation

@pytest.fixture
def command(monkeypatch,tmp_path):
    state=SimpleNamespace(calls=[],events=[],payload=b'{"schemaVersion":4,"opaque":"owned"}',path=tmp_path/'model/room.cfdrun')
    class Stream(io.BytesIO):
        def read(self,size=-1):
            assert state.events==['signal-handler-installed']
            return super().read(size)
    class Runner:
        def __init__(self,*args,**kwargs):state.calls.append((args,kwargs))
        def run_mesh_only(self):state.events.append('mesh')
        def run_solve_only(self):state.events.append('solve')
        def run_simulation(self):state.events.append('full')
    monkeypatch.setattr(implementation,'STLGenRunner',Runner)
    monkeypatch.setattr(cli,'_install_sigterm_handler',lambda _:state.events.append('signal-handler-installed'))
    state.stream=Stream(state.payload)
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=state.stream))
    def invoke(*flags,sha=True,stl_sha=True):
        argv=['run_cfd','--from-cfdrun',str(state.path),*flags]
        if sha:argv+=['--expected-cfdrun-sha256',hashlib.sha256(state.payload).hexdigest()]
        if stl_sha:argv+=['--expected-stl-batch-sha256','b'*64]
        monkeypatch.setattr(sys,'argv',argv)
        cli.main()
    state.invoke=invoke
    return state

@pytest.mark.parametrize('flag,operation',[('--mesh-only','mesh'),('--solve-only','solve'),(None,'full')])
def test_absent_replay_dispatches_same_owned_bytes_after_signal_setup(command,flag,operation):
    flags=['--cfdrun-stdin']+([flag] if flag else [])
    command.invoke(*flags)
    (case,path),kwargs=command.calls[0]
    assert case==command.path.parent.parent and path==command.path
    snapshot=kwargs['input_snapshot']
    assert snapshot.payload==command.payload and snapshot.logical_path==command.path
    assert snapshot.sha256==kwargs['expected_cfdrun_sha256'] and snapshot.stl_batch_sha256=='b'*64
    assert command.events==['signal-handler-installed',operation]
    assert not command.path.exists()

def test_wrong_stdin_hash_fails_before_runner(command):
    command.stream=io.BytesIO(b'changed bytes')
    sys.stdin.buffer=command.stream
    with pytest.raises(SystemExit) as error:command.invoke('--cfdrun-stdin')
    assert error.value.code==1 and not command.calls

def test_stdin_requires_hash_before_read_or_runner(command):
    with pytest.raises(SystemExit) as error:command.invoke('--cfdrun-stdin',sha=False)
    assert error.value.code==1 and not command.calls and command.stream.tell()==0

def test_legacy_file_entry_does_not_add_snapshot_keyword_or_read_stdin(command):
    command.path.parent.mkdir();command.path.write_bytes(command.payload)
    command.invoke('--mesh-only')
    _,kwargs=command.calls[0]
    assert 'input_snapshot' not in kwargs and command.stream.tell()==0
    assert command.events==['signal-handler-installed','mesh']


def test_stdin_requires_stl_batch_identity_before_read_or_runner(command):
    with pytest.raises(SystemExit) as error:command.invoke('--cfdrun-stdin',stl_sha=False)
    assert error.value.code==1 and not command.calls and command.stream.tell()==0
