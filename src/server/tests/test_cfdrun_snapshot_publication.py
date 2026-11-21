"""Atomic batch publication captures RAM bytes before the optional artifact."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

@pytest.fixture
def api(monkeypatch):
    path=Path(__file__).resolve().parents[1]/'server.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location('snapshot_publication_server',path)
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module);spec.loader.exec_module(module)
    return module

def content():
    return json.dumps({'schemaVersion':4,'runId':'snapshot-unit',
        'sourceProject':{'contentHash':'a'*64},'opaqueExtension':{'text':'温度'}})

def test_uploaded_batch_owns_serialized_bytes_without_cfdrun_read(api,tmp_path,monkeypatch):
    read_bytes=Path.read_bytes
    def forbid_manifest_read(path):
        assert path.suffix!='.cfdrun','fresh snapshot must not be reconstructed by reading its artifact'
        return read_bytes(path)
    monkeypatch.setattr(Path,'read_bytes',forbid_manifest_read)
    stl=b'solid unit\nendsolid unit\n'
    snapshot=api._publish_case_input_snapshot(tmp_path,'room.cfdrun',content(),
        {'room.stl':base64.b64encode(stl).decode()})
    assert snapshot.payload==read_bytes(snapshot.logical_path)
    assert snapshot.sha256==hashlib.sha256(snapshot.payload).hexdigest()
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
    assert snapshot.stl_batch_sha256==surface_batch_sha256(tmp_path)
    assert snapshot.parse()['opaqueExtension']=={'text':'温度'}
    assert snapshot.parse()['artifacts']['caseDir']==str(tmp_path.resolve())
    assert read_bytes(tmp_path/'Mesh/constant/triSurface/room.stl')==stl
    snapshot.logical_path.write_bytes(b'changed optional artifact')
    assert snapshot.parse()['runId']=='snapshot-unit'

def test_invalid_snapshot_refuses_before_old_input_is_replaced(api,tmp_path):
    model=tmp_path/'model';model.mkdir();old=model/'old.cfdrun';old.write_bytes(b'old owned bytes')
    tri=tmp_path/'Mesh/constant/triSurface';tri.mkdir(parents=True);(tri/'old.stl').write_bytes(b'old stl')
    with pytest.raises(ValueError,match='schemaVersion'):
        api._publish_case_input_snapshot(tmp_path,'new.cfdrun','{"schemaVersion":5}',
            {'new.stl':base64.b64encode(b'new stl').decode()})
    assert old.read_bytes()==b'old owned bytes'
    assert (tri/'old.stl').read_bytes()==b'old stl'
    assert not (model/'new.cfdrun').exists()

def test_existing_input_is_captured_once_under_case_lock(api,tmp_path,monkeypatch):
    path,sha=api._publish_case_inputs(tmp_path,'room.cfdrun',content(),{})
    original=Path.read_bytes;reads=[]
    def counted(p):
        if p.suffix=='.cfdrun':reads.append(p)
        return original(p)
    monkeypatch.setattr(Path,'read_bytes',counted)
    snapshot=api._publish_case_input_snapshot(tmp_path,None,None,{})
    assert snapshot.sha256==sha and reads==[path]
    (path.parent/'ambiguous.cfdrun').write_bytes(snapshot.payload)
    with pytest.raises(ValueError,match='exactly one'):
        api._publish_case_input_snapshot(tmp_path,None,None,{})


def test_full_pipeline_snapshot_accepts_explicit_stl_replacement(api,tmp_path):
    tri=tmp_path/'Mesh/constant/triSurface';tri.mkdir(parents=True)
    (tri/'stale.stl').write_bytes(b'stale surface')
    snapshot=api._publish_case_input_snapshot(tmp_path,'room.cfdrun',content(),{},replace_stls=True)
    assert snapshot.parse()['runId']=='snapshot-unit'
    assert tri.is_dir() and not (tri/'stale.stl').exists()


def test_invalid_stl_batch_capture_rolls_back_uploaded_manifest(api,tmp_path):
    model=tmp_path/'model';model.mkdir();old=model/'old.cfdrun';old.write_bytes(b'owned old manifest')
    tri=tmp_path/'Mesh/constant/triSurface';tri.mkdir(parents=True)
    target=tmp_path/'owned-target.stl';target.write_bytes(b'owned target')
    (tri/'alias.stl').symlink_to(target)
    with pytest.raises(ValueError,match='symlink'):
        api._publish_case_input_snapshot(tmp_path,'new.cfdrun',content(),{})
    assert old.read_bytes()==b'owned old manifest' and not (model/'new.cfdrun').exists()
    assert (tri/'alias.stl').is_symlink() and target.read_bytes()==b'owned target'


def original_upload(*, extra_field=False):
    stls={'one.stl':b'solid one\nendsolid one\n', 'two.stl':b'solid two\nendsolid two\n'}
    text=json.dumps({'schemaVersion':4,'runId':'real-upload-unit',
        'sourceProject':{'uid':'unit','contentHash':'b'*64},
        'geometryExport':{'stlFiles':{str(i):{'path':name,'contentHash':hashlib.sha256(data).hexdigest()}
            for i,(name,data) in enumerate(stls.items())}},'opaqueExtension':{'text':'温度'}},ensure_ascii=False)
    body={'cfdrunJson':text,'cfdrunFilename':'room.cfdrun','stlFiles':{
        name:base64.b64encode(data).decode() for name,data in stls.items()},'meshOnly':True,'solveOnly':False}
    if extra_field:body['legacyExtra']='must-not-be-retained'
    wire=(json.dumps(body,ensure_ascii=False,indent=1)+'\n').encode()
    return text,body,wire,stls


def test_read_body_returns_the_single_real_wire_read_without_reserializing(api):
    import io
    from types import SimpleNamespace
    _,body,wire,_=original_upload()
    stream=io.BytesIO(wire)
    handler=SimpleNamespace(headers={'Content-Length':str(len(wire))},rfile=stream)
    parsed,raw=api._read_body(handler,include_bytes=True)
    assert parsed==body and raw==wire and stream.tell()==len(wire)


def test_original_archive_keeps_real_request_and_stls_after_live_mutation(api,tmp_path):
    text,body,wire,stls=original_upload()
    input_id='83fe2ad7-30be-4a78-ac41-4c5986e1c088'
    snapshot=api._publish_case_input_snapshot(tmp_path,'room.cfdrun',text,body['stlFiles'],
        original_input_id=input_id,original_request=(wire,body))
    archive=tmp_path/'log/air-inputs'/input_id
    receipt=json.loads((archive/'INPUT.json').read_text())
    assert receipt['networkRequestBodyCaptured'] is True
    assert receipt['requestBody']['sha256']==hashlib.sha256(wire).hexdigest()
    assert (archive/'request.json').read_bytes()==wire
    assert (archive/'frontend/room.cfdrun').read_bytes()==text.encode()
    assert (archive/'model/room.cfdrun').read_bytes()==snapshot.payload
    assert json.loads(text)['sourceProject']==receipt['sourceProject']
    for name,raw in stls.items():
        live=tmp_path/'Mesh/constant/triSurface'/name
        live.write_bytes(b'normal runner transform');live.unlink()
        assert (archive/'Mesh/constant/triSurface'/name).read_bytes()==raw
    for row in receipt['files']:
        raw=(archive/row['path']).read_bytes()
        assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']


def test_original_archive_refuses_hash_mismatch_before_publication(api,tmp_path):
    api._publish_case_inputs(tmp_path,'old.cfdrun',content(),{'old.stl':base64.b64encode(b'old').decode()})
    text,body,wire,_=original_upload()
    body['stlFiles']['one.stl']=base64.b64encode(b'not declared bytes').decode()
    with pytest.raises(ValueError,match='content hash'):
        api._publish_case_input_snapshot(tmp_path,'room.cfdrun',text,body['stlFiles'],
            original_input_id='83fe2ad7-30be-4a78-ac41-4c5986e1c089',original_request=(wire,body))
    assert (tmp_path/'model/old.cfdrun').is_file()
    assert (tmp_path/'Mesh/constant/triSurface/old.stl').read_bytes()==b'old'
    assert not (tmp_path/'log/air-inputs').exists()


def test_original_archive_refuses_reuse_and_rolls_back_later_snapshot_failure(api,tmp_path,monkeypatch):
    text,body,wire,stls=original_upload()
    input_id='83fe2ad7-30be-4a78-ac41-4c5986e1c090'
    snapshot=api._publish_case_input_snapshot(tmp_path,'room.cfdrun',text,body['stlFiles'],
        original_input_id=input_id,original_request=(wire,body))
    archive=tmp_path/'log/air-inputs'/input_id
    with pytest.raises(ValueError,match='already exists'):
        api._publish_case_input_snapshot(tmp_path,'new.cfdrun',text,body['stlFiles'],
            original_input_id=input_id,original_request=(wire,body))
    assert (archive/'request.json').read_bytes()==wire
    assert (tmp_path/'model/room.cfdrun').read_bytes()==snapshot.payload
    from backend.aircooling.runner import cfdrun_stl_identity
    def fail(_):raise ValueError('injected snapshot failure')
    monkeypatch.setattr(cfdrun_stl_identity,'surface_batch_sha256',fail)
    next_id='83fe2ad7-30be-4a78-ac41-4c5986e1c091'
    with pytest.raises(ValueError,match='injected snapshot failure'):
        api._publish_case_input_snapshot(tmp_path,'new.cfdrun',text,body['stlFiles'],
            original_input_id=next_id,original_request=(wire,body))
    assert not (tmp_path/'log/air-inputs'/next_id).exists()
    assert (archive/'request.json').read_bytes()==wire
    assert (tmp_path/'model/room.cfdrun').read_bytes()==snapshot.payload
    assert not (tmp_path/'model/new.cfdrun').exists()
    for name,raw in stls.items():assert (tmp_path/'Mesh/constant/triSurface'/name).read_bytes()==raw


def test_unknown_body_fields_keep_public_inputs_but_do_not_claim_wire_capture(api,tmp_path):
    text,body,wire,stls=original_upload(extra_field=True)
    input_id='83fe2ad7-30be-4a78-ac41-4c5986e1c092'
    api._publish_case_input_snapshot(tmp_path,'room.cfdrun',text,body['stlFiles'],
        original_input_id=input_id,original_request=(wire,body))
    archive=tmp_path/'log/air-inputs'/input_id
    receipt=json.loads((archive/'INPUT.json').read_text())
    assert receipt['networkRequestBodyCaptured'] is False
    assert receipt['requestBody']['sha256']==hashlib.sha256(wire).hexdigest()
    assert receipt['requestBody']['bytes']==len(wire)
    assert not (archive/'request.json').exists()
    for p in archive.rglob('*'):
        if p.is_file():assert b'must-not-be-retained' not in p.read_bytes()
    for name,raw in stls.items():assert (archive/'Mesh/constant/triSurface'/name).read_bytes()==raw



def test_original_archive_retains_supported_click_elapsed_body_without_manifest_rewrite(api,tmp_path):
    text,body,_,stls=original_upload()
    body['meshPreparationElapsedSeconds']=61.25
    wire=(json.dumps(body,ensure_ascii=False,indent=2)+'\n').encode()
    input_id='86fe2ad7-30be-4a78-ac41-4c5986e1c088'
    api._publish_case_input_snapshot(tmp_path,'room.cfdrun',text,body['stlFiles'],
        original_input_id=input_id,original_request=(wire,body))
    archive=tmp_path/'log/air-inputs'/input_id
    assert (archive/'request.json').read_bytes()==wire
    assert (archive/'frontend/room.cfdrun').read_text()==text
    for name,data in stls.items():
        assert (archive/'Mesh/constant/triSurface'/name).read_bytes()==data
