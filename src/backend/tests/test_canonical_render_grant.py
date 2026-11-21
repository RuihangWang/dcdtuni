"""Controlled ownership/signature publication, never business acceptance."""
import copy,hashlib,json
from types import SimpleNamespace
import pytest
from backend.tests.test_canonical_render_packet import fixture as numeric_fixture,DESC,SESSION
from server import canonical_render_grant as mod
from server.ov_result_dataset import _grant_module
KEY='CONTROLLED launch-key-only-1234567890abcdef'

def fixture(tmp_path,monkeypatch):
 store,intent=numeric_fixture();run=tmp_path/'owned_run';run.mkdir()
 cpp=SimpleNamespace(run_dir=run,_numeric_product_store=store)
 monkeypatch.setattr(mod,'verify_render_source',lambda **_:copy.deepcopy(DESC))
 request={'schema':'dcdtuni.canonical-render-request/v1','scene':{'projectUid':DESC['source']['projectUid'],'projectContentSha256':DESC['source']['projectContentSha256'],'revision':3},'displayGeneration':5,'intent':intent}
 return cpp,request

def issue(cpp,request,**kw):return mod.issue_canonical_render_grant(cpp=cpp,case_dir=cpp.run_dir,session=SESSION,request=request,key=KEY,now=100,**kw)

def test_actual_file_publication_and_launch_key_signature_bind_same_numeric_products_source_frame_and_convergence(tmp_path,monkeypatch):
 cpp,request=fixture(tmp_path,monkeypatch);result=issue(cpp,request)
 verified=_grant_module().verify(result['grant'],KEY,now=101,roots=[str(tmp_path)],kind='rendererResult')
 from pathlib import Path
 raw=Path(verified['path']).read_bytes();packet=json.loads(raw)
 assert hashlib.sha256(raw).hexdigest()==result['packetReceipt']['sha256']==verified['packetSha256']
 assert packet['timeSeconds']==.02 and packet['source']==DESC['source']
 assert verified['runConvergence']==packet['runConvergence']=={'verdict':'NOT_CONVERGED','receiptSha256':'2'*64}
 assert issue(cpp,request)['packetReceipt']==result['packetReceipt']

@pytest.mark.parametrize('change',['scene_uid','scene_hash','case_before','case_after','source_after','product_after','artifact_after'])
def test_changes_before_signature_never_receive_a_grant(tmp_path,monkeypatch,change):
 cpp,request=fixture(tmp_path,monkeypatch)
 if change=='scene_uid':request['scene']['projectUid']='other'
 elif change=='scene_hash':request['scene']['projectContentSha256']='0'*64
 elif change.startswith('case_'):
  calls=0
  def current():
   nonlocal calls
   calls+=1
   return calls>1 if change=='case_before'else calls<2
  with pytest.raises(ValueError):issue(cpp,request,current_case=current)
  return
 else:
  calls=0
  def verify(**_):
   nonlocal calls
   calls+=1;out=copy.deepcopy(DESC)
   if calls==2:
    if change=='source_after':out['runConvergence']['verdict']='CONVERGED'
    elif change=='product_after':cpp._numeric_product_store.close()
    else:
     for file in (cpp.run_dir/'.ov-display/canonical-result').glob('*.json'):file.write_text('{}')
   return out
  monkeypatch.setattr(mod,'verify_render_source',verify)
 with pytest.raises(ValueError):issue(cpp,request)
