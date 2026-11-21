"""Controlled actual native reader + ordinary HTTP routes; no service/business solve.
511-cell mesh is literal; execution/fields/quality envelopes are synthetic fixtures.
"""
import copy,hashlib,importlib.util,io,json,sys,struct,time
from pathlib import Path
import pytest
from backend.aircooling.runner import postprocess as pp
from backend.tests.test_verified_render_source import fixture as source_fixture
from server.ov_result_dataset import _grant_module
KEY='CONTROLLED case binding launch key 0123456789'
class Handler:
 def __init__(self,body=None,revision=None,path=''):
  raw=json.dumps(body or {}).encode();self.headers={'Content-Length':str(len(raw))};self.path=path
  if revision:self.headers['X-PP-Revision']=revision
  self.rfile=io.BytesIO(raw);self.wfile=io.BytesIO();self.status=None;self.response_headers={}
 def send_response(self,status):self.status=status
 def send_header(self,name,value):self.response_headers[name]=value
 def end_headers(self):pass
 def json(self):return json.loads(self.wfile.getvalue())
@pytest.fixture
def fixture(tmp_path,monkeypatch):
 a=source_fixture(tmp_path/'caseA');b=source_fixture(tmp_path/'caseB')
 from backend.aircooling.runner.air_run_result_receipt import build_air_run_result_receipt
 rewritten=[]
 for i,item in enumerate([a,b]):
  case,oldrun,oldcpp,oldsession=item;run=oldrun.with_name(f'2026-10-06-11-00-0{i}_ControlledContractOnly');oldrun.rename(run)
  execution=json.loads((run/'air-run-execution-receipt.json').read_bytes());execution['result']=build_air_run_result_receipt(case_dir=case,cfdrun_path=case/'model/source.cfdrun',run_dir=run,time_value=.02);(run/'air-run-execution-receipt.json').write_text(json.dumps(execution));rewritten.append((case,run,oldcpp,oldsession))
 a,b=rewritten
 path=Path(__file__).resolve().parents[2]/'server/server.py';sys.path.insert(0,str(path.parent))
 spec=importlib.util.spec_from_file_location('scoped_pp_case_server',path);mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
 cache=pp.CachedPostProcessor();monkeypatch.setattr(mod,'_get_cached_pp',lambda:cache);monkeypatch.setattr(mod,'CASE_DIR',b[0]);monkeypatch.setattr(mod,'CASE_DIR_VERSION',31);monkeypatch.setenv('DCDTUNI_OV_GRANT_KEY',KEY)
 yield mod,cache,a,b
 cache.invalidate()
def initialize(mod,case):
 h=Handler({'caseDir':str(case),'runDir':str(next((case/'log').iterdir())),'timeValue':.02});mod.handle_pp_init(h);assert h.status==200,h.wfile.getvalue();return h.json()
def read(mod,route,session,*args):
 h=Handler(revision=session['revision']);getattr(mod,route)(h,*args);return h

def test_actual_cpp_initializes_explicit_caseA_while_other_tab_default_remains_caseB(fixture):
 mod,cache,a,b=fixture;session=initialize(mod,a[0]);assert mod.CASE_DIR==b[0] and mod.CASE_DIR_VERSION==31
 assert cache.run_dir==a[1] and session.get('caseDir')==str(a[0])
 assert session['runId']==hashlib.sha256(str(a[1].resolve()).encode()).hexdigest()
 assert cache.current_identity()['revision']==session['revision']
 field=read(mod,'handle_pp_field',session,'T');assert field.status==200 and field.response_headers['X-PP-Revision']==session['revision']
 meta=read(mod,'handle_pp_mesh',session);assert meta.status==200 and meta.json()['runId']==session['runId']
 source=read(mod,'handle_pp_render_source',session);assert source.status==200,source.wfile.getvalue()
 assert source.json()['postprocess']['runId']==session['runId']

def packet_request(mod,session):
 source=read(mod,'handle_pp_render_source',session);assert source.status==200,source.wfile.getvalue();desc=source.json()
 mesh=read(mod,'handle_pp_mesh_binary',session);field=read(mod,'handle_pp_field',session,'T');assert mesh.status==field.status==200
 mesh_token=json.loads(mesh.response_headers['X-PP-Product'])['token'];field_token=json.loads(field.response_headers['X-PP-Product'])['token']
 intent={'schema':'dcdtuni.common-render-intent/v1','revision':1,'postprocess':{k:session[k]for k in ['runId','timeValue','revision']},'source':desc['source'],'meshToken':mesh_token,'surface':{'fieldToken':field_token,'field':'T','lut':{'palette':'jet','range':[299,301],'opacity':.7},'hidden':[],'visible':True},'slices':[],'streamlines':[],'volume':None}
 return {'schema':'dcdtuni.canonical-render-request/v1','scene':{'projectUid':desc['source']['projectUid'],'projectContentSha256':desc['source']['projectContentSha256'],'revision':3},'displayGeneration':5,'intent':intent}

def test_real_cpp_owned_numeric_products_sign_bound_caseA_and_staleA_cannot_access_or_sign_caseB(fixture):
 mod,cache,a,b=fixture;first=initialize(mod,a[0]);request=packet_request(mod,first)
 grant=Handler(request,first['revision']);mod.handle_pp_render_grant(grant);assert grant.status==201,grant.wfile.getvalue()
 payload=_grant_module().verify(grant.json()['grant'],KEY,now=time.time(),roots=[str(a[0])],kind='rendererResult')
 assert Path(payload['path']).is_relative_to(a[1]) and payload['timeSeconds']==.02
 assert grant.json()['packetReceipt']['postprocess']['revision']==first['revision']
 second=initialize(mod,b[0]);assert second['revision']!=first['revision'] and second['runId']!=first['runId']
 for route,args in [('handle_pp_field',('T',)),('handle_pp_mesh',()),('handle_pp_mesh_binary',()),('handle_pp_render_source',())]:
  stale=read(mod,route,first,*args);assert stale.status==409 and stale.json()['code']=='pp_revision_stale'
 stale=Handler(request,first['revision']);mod.handle_pp_render_grant(stale);assert stale.status==409
 fresh_request=packet_request(mod,second);fresh=Handler(fresh_request,second['revision']);mod.handle_pp_render_grant(fresh);assert fresh.status==201,fresh.wfile.getvalue()
 new_payload=_grant_module().verify(fresh.json()['grant'],KEY,now=time.time(),roots=[str(b[0])],kind='rendererResult')
 assert Path(new_payload['path']).is_relative_to(b[1]) and new_payload['runId']==second['runId']

def test_invalid_explicit_case_and_outside_run_do_not_fall_back_or_replace_valid_cpp(fixture):
 mod,cache,a,b=fixture;session=initialize(mod,a[0]);before=cache.current_identity()
 for case in ['',True,'relative',str(a[0]/'absent')]:
  h=Handler({'caseDir':case,'runDir':'log/controlled'});mod.handle_pp_init(h);assert h.status==400 and h.json()['code']=='case_dir_invalid';assert cache.current_identity()==before
 h=Handler({'caseDir':str(a[0]),'runDir':str(b[1])});mod.handle_pp_init(h);assert h.status==400 and h.json()['code']=='pp_run_outside_case';assert cache.current_identity()==before

def test_request_scoped_scan_lists_only_requested_real_case_and_reports_scope(fixture):
 mod,cache,a,b=fixture
 for item in [a,b]:
  h=Handler(path='/api/scan-case-status?caseDir='+str(item[0]));mod.handle_scan_case_status(h);assert h.status==200
  body=h.json();assert body.get('caseDir')==str(item[0]);assert body['logRuns'][0]['runId']==hashlib.sha256(str(item[1].resolve()).encode()).hexdigest()
 assert mod.CASE_DIR==b[0]

def test_default_case_change_revokes_bound_revision_without_changing_any_job(fixture):
 mod,cache,a,b=fixture;first=initialize(mod,a[0]);mod.JOBS['controlled-held']=mod._new_job_state(['CONTROLLED held job contract; no subprocess'],caseDir=str(a[0]));held=mod.JOBS['controlled-held'];jobs={name:{key:copy.deepcopy(value)if isinstance(value,(dict,list,tuple,set))else value for key,value in job.items()}for name,job in mod.JOBS.items()}
 same=Handler({'caseDir':str(b[0]),'createIfMissing':False});mod.handle_set_case_dir(same);assert cache.current_identity()['revision']==first['revision']
 switch=Handler({'caseDir':str(a[0]),'createIfMissing':False});mod.handle_set_case_dir(switch);assert switch.status==200 and cache.scope_case_dir is None
 stale=read(mod,'handle_pp_field',first,'T');assert stale.status==409;assert mod.JOBS==jobs and mod.JOBS['controlled-held'] is held
