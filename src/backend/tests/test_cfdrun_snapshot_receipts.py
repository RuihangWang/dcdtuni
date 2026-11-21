"""RAM input identity parity for receipt APIs; native products are unit fixtures."""
import hashlib
import json
import pytest
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
from backend.aircooling.runner.air_mesh_quality_receipt import (
    record_prechecked_air_mesh_quality, verify_air_mesh_quality_receipt)
from backend.aircooling.runner.air_run_result_receipt import (
    build_air_run_result_receipt, verify_air_run_result_receipt)
from backend.aircooling.runner.air_run_execution_receipt import (
    preflight_air_run_execution_source, publish_air_run_execution_receipt,
    verify_air_run_execution_receipt)
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
from backend.tests.test_air_mesh_quality_receipt import _case as quality_case
from backend.tests.test_air_run_result_receipt import _case as result_case, _mesh, _write_time

def capture_quality(mesh, path, payload, verdict, **extra):
    return record_prechecked_air_mesh_quality(
        mesh, path, command=['checkMesh','-allTopology','-allGeometry'],
        workdir=mesh, mesh_sha256_before=mesh_identity_sha256(mesh),
        cfdrun_sha256_before=hashlib.sha256(payload).hexdigest(),
        returncode=0, stdout=verdict, stderr='', **extra)

@pytest.mark.parametrize('verdict', ['Mesh OK.\n', 'Failed 2 mesh checks.\n'])
def test_quality_ram_input_matches_file_receipt_without_artifact(tmp_path, verdict):
    mesh, path = quality_case(tmp_path)
    payload = path.read_bytes()
    expected = capture_quality(mesh,path,payload,verdict)
    snapshot = CfdrunSnapshot(payload,tmp_path,path)
    path.unlink()
    actual = capture_quality(mesh,path,payload,verdict,input_snapshot=snapshot)
    assert actual == expected
    assert verify_air_mesh_quality_receipt(tmp_path/'mesh-quality-receipt.json',
        mesh,path,input_snapshot=snapshot) == expected
    if expected['status'] == 'rejected':
        with pytest.raises(ValueError,match='rejected'):
            verify_air_mesh_quality_receipt(tmp_path/'mesh-quality-receipt.json',
                mesh,path,input_snapshot=snapshot,require_accepted=True)
    with pytest.raises(ValueError,match='existing file'):
        verify_air_mesh_quality_receipt(tmp_path/'mesh-quality-receipt.json',mesh,path)

def test_quality_ram_still_rejects_foreign_input_mesh_and_hash_drift(tmp_path):
    mesh,path=quality_case(tmp_path)
    payload=path.read_bytes(); snapshot=CfdrunSnapshot(payload,tmp_path,path)
    capture_quality(mesh,path,payload,'Mesh OK.\n',input_snapshot=snapshot)
    receipt=tmp_path/'mesh-quality-receipt.json'
    other=CfdrunSnapshot(payload+b' ',tmp_path,path)
    with pytest.raises(ValueError,match='identity'):
        verify_air_mesh_quality_receipt(receipt,mesh,path,input_snapshot=other)
    with pytest.raises(ValueError,match='artifact'):
        verify_air_mesh_quality_receipt(receipt,mesh,tmp_path/'model/other.cfdrun',input_snapshot=snapshot)
    (mesh/'constant/polyMesh/faces').write_bytes(b'changed mesh')
    with pytest.raises(ValueError,match='mesh identity'):
        verify_air_mesh_quality_receipt(receipt,mesh,path,input_snapshot=snapshot)

def prepared_result(tmp_path):
    case,path,run=result_case(tmp_path)
    raw=json.loads(path.read_bytes());raw['schemaVersion']=2
    path.write_text(json.dumps(raw))
    _mesh(run,'original');_write_time(run,'100')
    return case,path,run,CfdrunSnapshot(path.read_bytes(),case,path)

def test_result_ram_matches_complete_native_file_identity_without_artifact(tmp_path):
    case,path,run,snapshot=prepared_result(tmp_path)
    expected=build_air_run_result_receipt(case_dir=case,cfdrun_path=path,run_dir=run,time_value=100)
    path.unlink()
    actual=build_air_run_result_receipt(case_dir=case,cfdrun_path=path,run_dir=run,
        time_value=100,input_snapshot=snapshot)
    assert actual==expected
    assert verify_air_run_result_receipt(actual,case_dir=case,cfdrun_path=path,
        run_dir=run,input_snapshot=snapshot)==expected
    (run/'100/T').write_bytes((run/'100/T').read_bytes()+b'\n')
    with pytest.raises(ValueError,match='mismatch'):
        verify_air_run_result_receipt(actual,case_dir=case,cfdrun_path=path,
            run_dir=run,input_snapshot=snapshot)

def test_execution_ram_preflight_publish_and_replay_stay_bound(tmp_path):
    case,path,run,snapshot=prepared_result(tmp_path);path.unlink()
    preflight_air_run_execution_source(case_dir=case,cfdrun_path=path,input_snapshot=snapshot)
    receipt=publish_air_run_execution_receipt(case_dir=case,cfdrun_path=path,run_dir=run,
        solver_command='unit-fixture-successful-command',input_snapshot=snapshot)
    assert receipt['result']['cfdrunSha256']==snapshot.sha256
    assert verify_air_run_execution_receipt(receipt,case_dir=case,cfdrun_path=path,
        run_dir=run,input_snapshot=snapshot)==receipt
    other=CfdrunSnapshot(snapshot.payload+b' ',case,path)
    with pytest.raises(ValueError,match='mismatch'):
        verify_air_run_execution_receipt(receipt,case_dir=case,cfdrun_path=path,
            run_dir=run,input_snapshot=other)


def test_record_quality_ram_keeps_exact_strict_command(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.aircooling.runner import air_mesh_quality_receipt as quality
    mesh,path=quality_case(tmp_path)
    snapshot=CfdrunSnapshot(path.read_bytes(),tmp_path,path);path.unlink()
    calls=[]
    def completed(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,stdout='Mesh OK.\n',stderr='')
    monkeypatch.setattr(quality.subprocess,'run',completed)
    receipt=quality.record_air_mesh_quality(mesh,path,input_snapshot=snapshot)
    assert calls==[['checkMesh','-case',str(mesh.resolve()),'-constant','-allTopology','-allGeometry']]
    assert receipt['cfdrunSha256']==snapshot.sha256

def test_result_ram_rejects_case_local_artifact_symlink(tmp_path):
    case,path,run,snapshot=prepared_result(tmp_path)
    alias=path.with_name('alias.cfdrun');alias.symlink_to(path.name)
    with pytest.raises(ValueError,match='symlink'):
        build_air_run_result_receipt(case_dir=case,cfdrun_path=alias,run_dir=run,
            time_value=100,input_snapshot=snapshot)
