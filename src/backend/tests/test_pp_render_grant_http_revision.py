"""Actual HTTP wire201 headers with native CPP/store/HMAC; controlled fixture only.
No normal service/API, physical solve, browser or GPU validation.
"""
import copy,http.client,http.server,json,threading,time
from pathlib import Path
import pytest
from backend.tests.test_pp_case_binding import fixture,initialize,packet_request,KEY
from server.ov_result_dataset import _grant_module

def wire(mod,body,revision,*,ordinary=False):
 class Request(http.server.BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_POST(self):
   if ordinary:
    self._pp_identity={'runId':'controlled-current','timeValue':1,'revision':'controlled-current'}
    mod._send_json(self,200,{'ordinary':'no PP inference'})
   else:mod.handle_pp_render_grant(self)
 with http.server.ThreadingHTTPServer(('127.0.0.1',0),Request) as listener:
  worker=threading.Thread(target=listener.serve_forever,daemon=True);worker.start()
  connection=http.client.HTTPConnection('127.0.0.1',listener.server_port,timeout=10)
  try:
   connection.request('POST','/CONTROLLED-grant-test',body=json.dumps(body),headers={'Content-Type':'application/json','X-PP-Revision':revision})
   response=connection.getresponse();out={'status':response.status,'headers':dict(response.getheaders()),'body':json.loads(response.read())}
  finally:connection.close();listener.shutdown();worker.join(timeout=10)
 assert not worker.is_alive();return out

def test_successful_real_http201_exports_only_the_validated_revision_matching_actual_signed_receipt(fixture):
 mod,cache,a,b=fixture;session=initialize(mod,a[0]);request=packet_request(mod,session);reply=wire(mod,request,session['revision']);assert reply['status']==201,reply
 assert reply['headers'].get('X-PP-Revision')==session['revision']==reply['body']['packetReceipt']['postprocess']['revision']
 assert 'X-PP-Revision' in reply['headers'].get('Access-Control-Expose-Headers','')
 verified=_grant_module().verify(reply['body']['grant'],KEY,now=time.time(),roots=[str(a[0])],kind='rendererResult')
 assert verified['postprocessRevision']==session['revision'] and Path(verified['path']).is_relative_to(a[1])
 assert verified['runConvergence']['verdict']=='NOT_CONVERGED'
 assert mod.CASE_DIR==b[0] # The wire revision is not inferred from another tab's default directory.

@pytest.mark.parametrize('change',['run','time','source','revision'])
def test_wrong_identity_or_source_and_stale_revision_never_export_a_success_header_or_grant(fixture,change):
 mod,cache,a,b=fixture;session=initialize(mod,a[0]);request=packet_request(mod,session);selected=session['revision']
 if change=='revision':initialize(mod,b[0])
 elif change=='source':request['intent']['source']['projectContentSha256']='0'*64
 else:request['intent']['postprocess']['runId'if change=='run'else'timeValue']='other'if change=='run'else .01
 reply=wire(mod,request,selected);assert reply['status']==(409 if change=='revision' else 422),reply
 assert 'grant'not in reply['body'] and 'X-PP-Revision'not in reply['headers']

def test_ordinary_json_does_not_export_even_an_existing_handler_identity(fixture):
 mod,cache,a,b=fixture;reply=wire(mod,{},'ignored',ordinary=True);assert reply['status']==200
 assert 'X-PP-Revision'not in reply['headers'] and reply['body']=={'ordinary':'no PP inference'}
