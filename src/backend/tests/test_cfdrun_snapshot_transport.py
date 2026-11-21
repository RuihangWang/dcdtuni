"""Binary pipe semantics only; no CFD launcher, mesh or solver is executed."""
import hashlib
import io
import json
import subprocess
import sys
import pytest
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
from backend.aircooling.runner.cfdrun_transport import read_cfdrun_stdin, CfdrunInputFeeder

def snapshot(tmp_path, text='x'):
    payload=json.dumps({'schemaVersion':4,'extension':text},ensure_ascii=False).encode()
    return CfdrunSnapshot(payload,tmp_path,tmp_path/'model/input.cfdrun')

def receive(stream, source, expected=None):
    return read_cfdrun_stdin(stream,case_dir=source.case_dir,logical_path=source.logical_path,
        expected_sha256=source.sha256 if expected is None else expected)

def test_bounded_stdin_preserves_exact_utf8_and_logical_identity(tmp_path):
    source=snapshot(tmp_path,'温度\r\n含扩展')
    received=receive(io.BytesIO(source.payload),source)
    assert received.payload==source.payload and received.sha256==source.sha256
    assert not received.logical_path.exists()

@pytest.mark.parametrize('data', [b'',b'notJSON',b'{"schemaVersion":5}',b'\xff'])
def test_missing_truncated_or_invalid_input_has_no_file_fallback(tmp_path,data):
    source=snapshot(tmp_path)
    with pytest.raises(ValueError):
        receive(io.BytesIO(data),source)

def test_expected_identity_required_before_any_stdin_read(tmp_path):
    source=snapshot(tmp_path)
    class Untouched:
        def read(self,_):
            raise AssertionError('must validate expected identity before reading')
    with pytest.raises(ValueError,match='expected'):
        read_cfdrun_stdin(Untouched(),case_dir=tmp_path,logical_path=source.logical_path,
            expected_sha256=None)

def test_transfer_capacity_refuses_one_byte_over_bound(tmp_path,monkeypatch):
    import backend.aircooling.runner.cfdrun_transport as transport
    monkeypatch.setattr(transport,'MAX_CFDRUN_SNAPSHOT_BYTES',64)
    source=snapshot(tmp_path)
    with pytest.raises(ValueError,match='capacity'):
        receive(io.BytesIO(b'x'*65),source)

def test_stdout_backpressure_cannot_block_simultaneous_input_feed(tmp_path):
    source=snapshot(tmp_path,'x'*300000+'温度')
    code='import sys,hashlib;sys.stdout.buffer.write(b"X"*300000);sys.stdout.flush();raw=sys.stdin.buffer.read();print(hashlib.sha256(raw).hexdigest())'
    proc=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    feeder=CfdrunInputFeeder(proc.stdin,source);feeder.start()
    output=proc.stdout.read();assert proc.wait(timeout=3)==0;feeder.join(3)
    assert not feeder.is_alive() and feeder.error is None
    assert output==b'X'*300000+source.sha256.encode()+b'\n'

def test_child_exit_releases_blocked_feeder_and_keeps_error(tmp_path):
    source=snapshot(tmp_path,'x'*300000)
    proc=subprocess.Popen([sys.executable,'-c','pass'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    feeder=CfdrunInputFeeder(proc.stdin,source);feeder.start()
    assert proc.wait(timeout=3)==0;feeder.join(3)
    assert not feeder.is_alive() and feeder.error is not None
