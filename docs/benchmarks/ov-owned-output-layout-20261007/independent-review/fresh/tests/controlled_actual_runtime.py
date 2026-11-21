from pathlib import Path
import copy,hashlib,importlib,importlib.util,json,math,sys,types,weakref
from pxr import Gf,Usd,UsdGeom,UsdShade,Sdf,CameraUtil
D=Path(__file__).resolve().parents[1]
BR=D/'candidate/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
for name in ['omni','omni.ext','omni.usd','omni.kit','omni.kit.app','omni.appwindow','omni.kit.viewport','omni.kit.viewport.utility','carb','carb.events','carb.eventdispatcher','carb.settings']:
 m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m
 if '.' in name:setattr(sys.modules[name.rsplit('.',1)[0]],name.rsplit('.',1)[1],m)
sys.modules['omni.ext'].IExt=object
for name in ['controlled_bridge','literal_viewport','literal_viewport.impl']:
 m=types.ModuleType(name);m.__path__=[str(BR) if name=='controlled_bridge' else str(D/'harness')];sys.modules[name]=m
util=types.ModuleType('literal_viewport.impl.utility');util._report_error=lambda *_:None;sys.modules[util.__name__]=util
capture=types.ModuleType('literal_viewport.capture');capture.Capture=object;sys.modules[capture.__name__]=capture
contexts={}
settings=types.SimpleNamespace(get=lambda _:None,subscribe_to_node_change_events=lambda *_:None,unsubscribe_to_change_events=lambda *_:None)
sys.modules['carb.settings'].get_settings=lambda:settings;sys.modules['carb.settings'].SubscriptionId=object
sys.modules['omni.usd'].get_context=lambda name=None:contexts[name]
spec=importlib.util.spec_from_file_location('literal_viewport.api',D/'harness/ViewportAPI.py');api_mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api_mod;spec.loader.exec_module(api_mod)
assert hashlib.sha256((D/'harness/ViewportAPI.py').read_bytes()).hexdigest()=='0f2ed3f0b60162aca96ab7d0bac983f4735a88e786acfd6c77ce0c348d01a22c'
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
 def subscribe_to_node_change_events(self,*args):return object()
 def unsubscribe_to_change_events(self,*args):pass
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

