"""Actual native CPP products and CPU USD; controlled uniform fields/receipts only.
No native solve, service, GPU, browser or convergence acceptance.
"""
import base64,copy,hashlib,importlib.util,json,struct,time
from pathlib import Path
import pytest
from backend.tests.test_pp_case_binding import fixture as base_fixture,initialize,read,Handler,KEY
from backend.aircooling.runner.air_run_result_receipt import build_air_run_result_receipt
from server.canonical_render_packet import build_canonical_packet
from server.ov_result_dataset import _grant_module

@pytest.fixture
def fixture(base_fixture):
 mod,cpp,a,b=base_fixture
 first=initialize(mod,a[0]);names=read(mod,'handle_pp_render_source',first).json()['boundaryProvenanceByPatch']
 # Explicit real final patch names, no fabricated UID/region provenance. The
 # entire field/execution envelope remains synthetic CONTROLLED input.
 for t in [.01,.02]:
  for name,value,kind,dimensions in [('T','300','volScalarField','[0 0 0 1 0 0 0]'),('U','(0.1 0 0)','volVectorField','[0 1 -1 0 0 0 0]')]:
   text='FoamFile { version 2.0; format ascii; class '+kind+'; object '+name+'; }\ndimensions '+dimensions+';\ninternalField uniform '+value+';\nboundaryField {\n'+''.join(p+' { type zeroGradient; }\n'for p in names)+'}\n'
   (a[1]/str(t)/name).write_text(text)
 execution=json.loads((a[1]/'air-run-execution-receipt.json').read_bytes());execution['result']=build_air_run_result_receipt(case_dir=a[0],cfdrun_path=a[0]/'model/source.cfdrun',run_dir=a[1],time_value=.02);(a[1]/'air-run-execution-receipt.json').write_text(json.dumps(execution));cpp.invalidate()
 session=initialize(mod,a[0]);desc=read(mod,'handle_pp_render_source',session).json();snapshot={'initResponse':session,'descriptor':desc}
 for kind,route,args in [('mesh','handle_pp_mesh_binary',()),('field','handle_pp_field',('T',))]:
  h=read(mod,route,session,*args);assert h.status==200
  snapshot[kind]={'base64':base64.b64encode(h.wfile.getvalue()).decode(),'receipt':json.loads(h.response_headers['X-PP-Product'])}
 snapshot['mesh']['meta']=read(mod,'handle_pp_mesh',session).json();snapshot['field']['meta']={'nPoints':snapshot['mesh']['meta']['totalPoints']}
 center=[float(v)for v in cpp.get_internal_mesh().cell_centers().points[0]]
 for kind,route,request in [('slice','handle_pp_slice',{'normal':[0,0,1],'origin':[1,1,1],'field':'T'}),('streamline','handle_pp_streamlines',{'center':center,'radius':.01,'nSeeds':2,'field':'T'})]:
  h=Handler(request,session['revision']);getattr(mod,route)(h);assert h.status==200,h.wfile.getvalue()
  snapshot[kind]={'base64':base64.b64encode(h.wfile.getvalue()).decode(),'receipt':json.loads(h.response_headers['X-PP-Product']),'request':request}
 yield mod,cpp,a,b,session,desc,snapshot

def intent_of(snapshot):
 s=snapshot['descriptor']['postprocess'];lut={'palette':'jet','range':[300,300],'opacity':.7}
 return {'schema':'dcdtuni.common-render-intent/v1','revision':1,'postprocess':copy.deepcopy(s),'source':copy.deepcopy(snapshot['descriptor']['source']),'meshToken':snapshot['mesh']['receipt']['token'],'surface':{'fieldToken':snapshot['field']['receipt']['token'],'field':'T','lut':copy.deepcopy(lut),'hidden':[],'visible':True},'slices':[{'setId':3,'token':snapshot['slice']['receipt']['token'],'sourceUid':s['runId']+'/time-0.02/slice-3','lut':copy.deepcopy(lut),'visible':True}],'streamlines':[{'setId':7,'token':snapshot['streamline']['receipt']['token'],'sourceUid':s['runId']+'/time-0.02/seed-7','lut':copy.deepcopy(lut),'visible':True,'tubeRadiusMeters':.01}],'volume':None}

def adapter():
 path=Path(__file__).resolve().parents[2]/'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/result_layers_usd.py'
 spec=importlib.util.spec_from_file_location('constant_lut_usd',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_actual_native_uniform_surface_cut_and_trace_preserve_flat_ranges_through_signed_packet_and_real_usd(fixture):
 mod,cpp,a,b,s,desc,snapshot=fixture
 field=base64.b64decode(snapshot['field']['base64']);assert struct.unpack_from('<ff',field)==(300,300)
 packet=build_canonical_packet(intent_of(snapshot),descriptor=desc,store=cpp._numeric_product_store)
 scalar=[p for p in packet['layers']if p['kind']!='meshEdges'];assert {'surfaceField','slice','streamline'}<=set(p['kind']for p in scalar)
 request={'schema':'dcdtuni.canonical-render-request/v1','scene':{'projectUid':desc['source']['projectUid'],'projectContentSha256':desc['source']['projectContentSha256'],'revision':3},'displayGeneration':5,'intent':intent_of(snapshot)}
 h=Handler(request,s['revision']);mod.handle_pp_render_grant(h);assert h.status==201,h.wfile.getvalue()
 grant=_grant_module().verify(h.json()['grant'],KEY,now=time.time(),roots=[str(a[0])],kind='rendererResult');literal=Path(grant['path']).read_bytes();assert hashlib.sha256(literal).hexdigest()==h.json()['packetReceipt']['sha256'];assert json.loads(literal)==packet
 usd=adapter();from pxr import Usd,UsdGeom,UsdShade
 stage=Usd.Stage.CreateInMemory();out=usd.author_result_layers(stage,packet,expected_source=desc['source'],expected_time_seconds=.02)
 for layer in scalar:
  assert layer['lut']['range']==[300,300]
  raw=layer['values']if 'values'in layer else [v for line in layer['lines']for v in line['values']];assert raw and set(raw)=={300}
  prim=stage.GetPrimAtPath(out['layerPaths'][layer['layerId']]);assert list(prim.GetCustomDataByKey('dcdtuni:lutRange'))==[300,300]
  assert list(UsdGeom.PrimvarsAPI(prim).GetPrimvar('dcdtuniRawScalar').Get())==raw
  colors=UsdGeom.PrimvarsAPI(prim).GetPrimvar('displayColor').Get();assert all(tuple(round(c*255)for c in rgb)==(0,0,128)for rgb in colors)
  material=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0];assert material.GetPrim().GetChild('Surface').GetAttribute('inputs:opacity').Get()==pytest.approx(.7)
 assert packet['runConvergence']['verdict']=='NOT_CONVERGED'

@pytest.mark.parametrize('change',['run','frame','revision','source','token','reversed','bool','nan','opacity','native_mutation'])
def test_flat_lut_does_not_relax_pinned_numeric_or_source_gates(fixture,change):
 mod,cpp,a,b,s,desc,snapshot=fixture;intent=intent_of(snapshot)
 if change in ['run','frame','revision']:intent['postprocess']['runId'if change=='run'else'timeValue'if change=='frame'else'revision']='other'if change!='frame'else .01
 elif change=='source':intent['source']['cfdrunSha256']='0'*64
 elif change=='token':intent['surface']['fieldToken']=intent['meshToken']
 elif change=='reversed':intent['surface']['lut']['range']=[301,300]
 elif change=='bool':intent['surface']['lut']['range']=[True,True]
 elif change=='nan':intent['surface']['lut']['range']=[float('nan'),float('nan')]
 elif change=='opacity':intent['surface']['lut']['opacity']=True
 else:(a[1]/'0.02/T').write_bytes((a[1]/'0.02/T').read_bytes()+b'\n')
 request={'schema':'dcdtuni.canonical-render-request/v1','scene':{'projectUid':desc['source']['projectUid'],'projectContentSha256':desc['source']['projectContentSha256'],'revision':3},'displayGeneration':5,'intent':intent}
 h=Handler(request,s['revision']);mod.handle_pp_render_grant(h);assert h.status in [409,422]and 'grant'not in h.json()

@pytest.mark.parametrize('palette,endpoint',[('jet',(0,0,128)),('coolwarm',(59,76,192)),('viridis',(68,1,84))])
def test_actual_usd_flat_normalization_is_finite_and_matches_common_palette_endpoint(palette,endpoint):
 colors=adapter()._colors([300,300,300],{'palette':palette,'range':[300,300],'opacity':.7})
 assert len(colors)==3 and all(tuple(round(channel*255)for channel in color)==endpoint for color in colors)

@pytest.mark.parametrize('palette,endpoint',[('jet',(0,0,128)),('coolwarm',(59,76,192)),('viridis',(68,1,84))])
def test_collapsed_display_domain_and_outliers_sample_only_palette_start_without_rewriting_field(palette,endpoint):
 raw=[300,350,299];lut={'palette':palette,'range':[300,300],'opacity':.7};colors=adapter()._colors(raw,lut)
 assert raw==[300,350,299]and lut['range']==[300,300]
 assert all(tuple(round(channel*255)for channel in color)==endpoint for color in colors)

@pytest.mark.parametrize('domain,flat', [([315,315],1.),([315,315+1e-8],0.),([315,331],0.)])
def test_actual_volume_usd_binds_flat_decision_from_literal_domain_before_shader_rounding(tmp_path,monkeypatch,domain,flat):
 from backend.tests.test_common_volume_grant import fixture as volume_fixture,issue,controller_module,KEY as VOLUME_KEY
 from pxr import Usd
 cpp,request,original=volume_fixture(tmp_path,monkeypatch);request['intent']['volume']['lut']['range']=domain
 signed=issue(cpp,request);verified=_grant_module().verify(signed['grant'],VOLUME_KEY,now=101,roots=[str(tmp_path)],kind='rendererResult');packet=controller_module.read_granted_packet(verified)
 assert packet['volume']['lut']['range']==domain
 originalPath=Path(verified['path']).with_name(packet['volume']['artifactName']);assert originalPath.read_bytes()==original
 # Explicit placeholder validates USD parameter lowering only, not nvdb or GPU.
 carrier=tmp_path/'CONTROLLED_CPU_ONLY_PLACEHOLDER.nvdb';carrier.write_bytes(b'CPU_ONLY_NOT_NVDB')
 stage=Usd.Stage.CreateInMemory();receipt=adapter().author_result_layers(stage,packet,expected_source=verified['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 prim=stage.GetPrimAtPath(receipt['layerPaths']['volume']+'/Volume');assert tuple(prim.GetCustomDataByKey('dcdtuni:rawLutDomain'))==tuple(domain)
 attribute=stage.GetPrimAtPath(receipt['layerPaths']['volume']+'/Volume/Material/VolumeShader').GetAttribute('inputs:flatDomain');assert attribute.Get()==flat and attribute.GetCustomDataByKey('nvindex.param')==6
 assert packet['runConvergence']['verdict']=='NOT_CONVERGED'
