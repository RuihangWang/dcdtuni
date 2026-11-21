from pathlib import Path
import copy,hashlib,importlib,importlib.util,json,math,sys,types,weakref
from pxr import Gf,Usd,UsdGeom,UsdShade,Sdf,CameraUtil
D=Path(__file__).resolve().parents[1]
BR=D/'runtime/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
for name in ['omni','omni.ext','omni.usd','omni.kit','omni.kit.app','omni.appwindow','omni.kit.viewport','omni.kit.viewport.utility','carb','carb.events','carb.eventdispatcher','carb.settings']:
 m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m
 if '.' in name:setattr(sys.modules[name.rsplit('.',1)[0]],name.rsplit('.',1)[1],m)
sys.modules['omni.ext'].IExt=object
for name in ['controlled_bridge','literal_viewport','literal_viewport.impl']:
 m=types.ModuleType(name);m.__path__=[str(BR) if name=='controlled_bridge' else str(D/'dependency')];sys.modules[name]=m
util=types.ModuleType('literal_viewport.impl.utility');util._report_error=lambda *_:None;sys.modules[util.__name__]=util
capture=types.ModuleType('literal_viewport.capture');capture.Capture=object;sys.modules[capture.__name__]=capture
contexts={}
settings=types.SimpleNamespace(get=lambda _:None,subscribe_to_node_change_events=lambda *_:None,unsubscribe_to_change_events=lambda *_:None)
sys.modules['carb.settings'].get_settings=lambda:settings;sys.modules['carb.settings'].SubscriptionId=object
sys.modules['omni.usd'].get_context=lambda name=None:contexts[name]
spec=importlib.util.spec_from_file_location('literal_viewport.api',D/'dependency/ViewportAPI.py');api_mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api_mod;spec.loader.exec_module(api_mod)
assert hashlib.sha256((D/'dependency/ViewportAPI.py').read_bytes()).hexdigest()=='0f2ed3f0b60162aca96ab7d0bac983f4735a88e786acfd6c77ce0c348d01a22c'
from controlled_bridge import display_diagnostics as p,grid_viewport as grid,render_scene as r,extension as ext
checks=[]
def check(label,fn):fn();checks.append(label)
UID='floor-uid';H='a'*64;geo='/World/RenderScene/Geometry/p_'+'1'*24;mat='/World/RenderScene/Looks/p_'+'1'*24
class App:
 def get_size(self):return types.SimpleNamespace(x=1600,y=638)
 def get_position(self):return types.SimpleNamespace(x=0,y=0)
 def get_dpi_scale(self):return 1
 def get_ui_scale(self):return 1
class Outer:
 frame=types.SimpleNamespace(screen_position_x=0,screen_position_y=0,computed_content_width=1600,computed_content_height=638)
 viewport_widget=types.SimpleNamespace(name='')
class SettingCarrier:
 def __init__(self):self.calls=[];self.values={k:False for k in p.SETTINGS.values()}
 def get(self,k):self.calls.append(k);return self.values.get(k)
def payload(uid=UID):return json.dumps({'requestId':'probe','diagnostics':{'profile':p.PROFILE,'expectedSceneRevision':1,'expectedProjectHash':H,**({} if uid is None else {'roofModelUid':uid})}},separators=(',',':'))
def fixture():
 s=Usd.Stage.CreateInMemory();UsdGeom.Xform.Define(s,r.ROOT);r.apply_view(s,{'camera':{'position':[0,20,0],'target':[0,0,0],'up':[0,0,-1],'fov':45,'near':.1,'far':400,'aspect':1600/638},'selectedUids':[],'timeSeconds':None})
 g=UsdGeom.Mesh.Define(s,geo).GetPrim();g.SetCustomDataByKey('dcdtuni:modelUid',UID)
 m=UsdShade.Material.Define(s,mat);surface=UsdShade.Shader.Define(s,mat+'/Surface');surface.CreateIdAttr('UsdPreviewSurface')
 for name,val in [('opacity',.15),('metallic',.35),('opacityThreshold',0)]:surface.CreateInput(name,Sdf.ValueTypeNames.Float).Set(val)
 surface.CreateOutput('surface',Sdf.ValueTypeNames.Token);m.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(),'surface');UsdShade.MaterialBindingAPI.Apply(g).Bind(m)
 name=s.GetRootLayer().identifier;contexts[name]=types.SimpleNamespace(get_stage=lambda:s)
 a=api_mod.ViewportAPI(name,'CONTROLLED_NATIVE_TEXTURE',None);a._ViewportAPI__viewport_texture=types.SimpleNamespace(resolution=(1600,900),full_resolution=(1600,900),camera_path=r.CAMERA);a._sync_viewport_api(UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA)),(1600,638),force_update=True)
 state={'stage':s,'sceneRevision':1,'projectHash':H,'displayGeneration':7,'uidPaths':{UID:[geo]}};setting=SettingCarrier();calls={'pair':0,'sampler':0,'view':0}
 def pair():calls['pair']+=1;return weakref.proxy(a),Outer()
 def sample(*args,**kw):calls['sampler']+=1;return grid.sample_grid_viewport(*args,**kw)
 def view():calls['view']+=1;return r.read_view(s,[])
 def run(controller=None,message=None,sampler=None,read_view=None,context=None):return (controller or p.ReadbackController(True)).read(message or payload(),context or (lambda:state),read_view or view,pair,setting,lambda:App(),sampler or sample)
 return s,a,state,setting,calls,run

def positive():
 s,a,state,settings,calls,run=fixture();before=s.GetRootLayer().ExportToString();values=copy.deepcopy(settings.values);reply=run();assert reply['ok'],reply
 diag=reply['diagnostics'];assert diag['pixelVerified']is False;assert diag['viewport']['layoutProof']=='unresolved';assert diag['viewport']['cameraPath']==r.CAMERA;assert len(diag['viewport']['worldToNdc'])==16;assert any(not x['insideTexture']for x in diag['viewport']['ndcToTextureSamples']);assert diag['viewport']['resolution']==[1600,900];assert diag['roof']['status']=='observed';assert calls=={'pair':1,'sampler':1,'view':1};assert s.GetRootLayer().ExportToString()==before and settings.values==values
 (D/'actual-native-positive-reply.json').write_text(json.dumps(reply,indent=2));assert len(p.bounded_json(reply).encode())<=16384
check('actual literal public API+real CPU USD sampler/material/readonly positive',positive)
def defaultoff():
 for enabled in [False,None,0,1,'true','bounded-display-v1']:
  s,a,state,setting,calls,run=fixture();reply=run(p.ReadbackController(enabled));assert reply['ok']is False and reply['error']=='bounded-display:disabled';assert not any(calls.values()) and not setting.calls
check('startup strict boolean capture: all false-like/string/numeric values do zero producer work',defaultoff)
def invalid_requests():
 base=json.loads(payload());changes=[dict(base,path='/foreign'),dict(base,execute='1')]
 for key,val in [('profile','other'),('expectedSceneRevision',True),('expectedSceneRevision',-1),('expectedProjectHash','z'*64),('roofModelUid','/foreign')]:v=copy.deepcopy(base);v['diagnostics'][key]=val;changes.append(v)
 for v in changes:
  s,a,state,setting,calls,run=fixture()
  try:run(message=json.dumps(v));raise AssertionError('must refuse')
  except (ValueError,TypeError):pass
  assert not any(calls.values()) and not setting.calls
 raw=payload();assert p.parse_request(raw+' '*(1024-len(raw.encode())))['requestId']=='probe'
 try:p.parse_request(raw+' '*(1025-len(raw.encode())));raise AssertionError('1025 must refuse')
 except ValueError:pass
check('exact request whitelist/profile/UID/hash/number and 1024/1025 before all producers',invalid_requests)
def limits():
 # Actual serializer cap; these are boundary serialization controls, not schema-valid material ACKs.
 assert len(p.bounded_json({'x':'a'*(16384-8)}).encode())==16384
 try:p.bounded_json({'x':'a'*(16385-8)});raise AssertionError('reply cap')
 except ValueError:pass
 s,a,state,setting,calls,run=fixture();reply=run(read_view=lambda:{'selectedUids':['😀'*128]*8,'timeSeconds':None});assert not reply['ok'] and reply['error']=='bounded-display:base-view-limit';assert calls['sampler']==0
 for bad in [{'selectedUids':{'nonempty':'foreign'},'timeSeconds':None},{'selectedUids':[],'timeSeconds':float('nan')},{'selectedUids':[],'timeSeconds':None,'asset':'/secret'}]:
  s,a,state,setting,calls,run=fixture();assert not run(read_view=lambda:bad)['ok'];assert calls['sampler']==0
check('native reply/base-view/NaN/unknown/raw-shape byte refusal is never truncated',limits)
def source_mutations():
 for mutation in [lambda st:st.update(sceneRevision=2),lambda st:st.update(projectHash='b'*64),lambda st:st.update(displayGeneration=8),lambda st:st['uidPaths'][UID].append(geo),lambda st:st.update(stage=Usd.Stage.CreateInMemory())]:
  s,a,state,setting,calls,run=fixture()
  def sample(*args,**kw):out=grid.sample_grid_viewport(*args,**kw);mutation(state);return out
  reply=run(sampler=sample);assert not reply['ok'] and reply['error']=='bounded-display:source-context-changed',reply
 s,a,state,setting,calls,run=fixture()
 def sample(*args,**kw):out=grid.sample_grid_viewport(*args,**kw);s.GetPrimAtPath(geo).SetCustomDataByKey('dcdtuni:modelUid','other');return out
 assert not run(sampler=sample)['ok']
 s,a,state,setting,calls,run=fixture()
 def sample(*args,**kw):out=grid.sample_grid_viewport(*args,**kw);s.GetPrimAtPath(mat+'/Surface').GetAttribute('inputs:opacity').Set(.5);return out
 assert not run(sampler=sample)['ok']
 s,a,state,setting,calls,run=fixture()
 def sample(*args,**kw):out=grid.sample_grid_viewport(*args,**kw);a._ViewportAPI__viewport_texture.camera_path='/World/Other';return out
 assert not run(sampler=sample)['ok']
check('mutable stage/revision/hash/generation/UID map/model/material/camera identity refuses after getters',source_mutations)
def role_negatives():
 for uid in [None,'missing']:
  s,a,state,setting,calls,run=fixture();reply=run(message=payload(uid));assert reply['ok'] and reply['diagnostics']['roof']['status']=='unavailable'
 s,a,state,setting,calls,run=fixture();state['uidPaths'][UID]=[geo]*5;reply=run();assert not reply['ok'];assert calls['sampler']==0
 s,a,state,setting,calls,run=fixture();s.GetPrimAtPath(geo).GetRelationship('material:binding').SetTargets(['/World/Foreign']);reply=run();assert reply['ok'] and reply['diagnostics']['roof']['reason']=='not-owned-binding'
 s,a,state,setting,calls,run=fixture();s.GetPrimAtPath(mat+'/Surface').GetAttribute('info:id').Set('ArbitraryShader');assert run()['diagnostics']['roof']['reason']=='unsupported-shader'
check('missing/fanout/non-owned binding/unsupported shader remain honest, no name/.15 guesses',role_negatives)
def assets():
 s,a,state,setting,calls,run=fixture();tex=UsdShade.Shader.Define(s,mat+'/ColorTexture');tex.CreateIdAttr('UsdUVTexture');tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('/secret/do-not-open.png'));tex.CreateInput('scale',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(1));tex.CreateInput('bias',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(0));tex.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('sRGB');tex.CreateOutput('a',Sdf.ValueTypeNames.Float);surface=UsdShade.Shader(s.GetPrimAtPath(mat+'/Surface'));surface.GetInput('opacity').ConnectToSource(tex.ConnectableAPI(),'a')
 original=Usd.Attribute.Get;calls_asset=[]
 def guarded(attr,*args,**kw):
  if attr.GetName()in ('inputs:file','info:sourceAsset'):calls_asset.append(str(attr.GetPath()));raise AssertionError('asset getter prohibited')
  return original(attr,*args,**kw)
 Usd.Attribute.Get=guarded
 try:
  before=s.GetRootLayer().ExportToString();reply=run();assert reply['ok'];roof=reply['diagnostics']['roof'];assert roof['materials'][0]['opacityTexture']['assetInspection']=='not-requested';assert roof['materials'][0]['opacity']['connected']is True;assert not calls_asset;assert '/secret' not in p.bounded_json(reply);assert s.GetRootLayer().ExportToString()==before
 finally:Usd.Attribute.Get=original
check('direct one-hop opacity metadata: actual USD asset Get forbidden, no file/path/resolve/author writes',assets)
def public_bad():
 for resolution in [(0,900),(40000,900),(float('nan'),900)]:
  s,a,state,setting,calls,run=fixture();a._ViewportAPI__viewport_texture.resolution=resolution;reply=run();assert reply['ok'];assert reply['diagnostics']['viewport']['status']in ('pending','unavailable');assert reply['diagnostics']['viewport']['layoutProof']=='unresolved'
 s,a,state,setting,calls,run=fixture();setting.values[next(iter(setting.values))]=0;reply=run();assert reply['diagnostics']['renderer']['settings']['translucencyAsOpacity']['reason']=='unexpected-setting-type'
check('invalid/zero/oversize/NaN public values and numeric false setting never clamp/default',public_bad)
def rate():
 s,a,state,setting,calls,run=fixture();now=[10.];controller=p.ReadbackController(True,lambda:now[0]);assert run(controller)['ok'];assert run(controller)is None;now[0]+=1;assert run(controller)['ok'];assert calls['sampler']==2
 s,a,state,setting,calls,run=fixture();controller=p.ReadbackController(True);nested=[]
 def sample(*args,**kw):nested.append(run(controller));return grid.sample_grid_viewport(*args,**kw)
 assert run(controller,sampler=sample)['ok'];assert nested==[None]
check('one in-flight/one-per-second admission returns no per-request flood ACK',rate)
def actual_route():
 s,a,state,setting,calls,run=fixture();contexts[None]=types.SimpleNamespace(get_stage=lambda:s,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:[]));sys.modules['carb.settings'].get_settings=lambda:setting;sys.modules['omni.kit.viewport.utility'].get_active_viewport_and_window=lambda:(weakref.proxy(a),Outer());sys.modules['omni.appwindow'].get_default_app_window=lambda:App()
 c=ext.CfdBridgeExtension();c._render_uid_paths=state['uidPaths'];c._scene_revision=1;c._render_project_hash=H;c._last_scene_request_id='scene';c._display_generation=7;c._bounded_display_readback=p.ReadbackController(True);acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
 c._route_render_command('getViewState',payload());assert acks[-1]['ok'] and 'diagnostics'in acks[-1];assert acks[-1]['diagnostics']['viewport']['layoutProof']=='unresolved'
 setting.calls.clear();c._route_render_command('getViewState',json.dumps({'requestId':'plain'}));assert acks[-1]['ok'] and 'diagnostics'not in acks[-1] and not setting.calls
 oldlen=len(acks);c._route_render_command('getViewState',' '*1025);assert len(acks)==oldlen
check('actual CfdBridgeExtension route exact optional args and plain recovery branch unchanged',actual_route)
print(json.dumps({'checks':checks,'count':len(checks),'scope':'CONTROLLED event carrier + literal installed public API + actual CPU USD. No live Kit/GPU/browser/pixel proof'},indent=2))
(D/'native-runtime-results.json').write_text(json.dumps({'checks':checks,'count':len(checks)},indent=2)+'\n')
